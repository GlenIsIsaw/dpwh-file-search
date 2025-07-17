import os
import time
import threading
import zipfile
from flask import Flask, render_template_string, request, send_from_directory, jsonify, Response
import webbrowser
from datetime import datetime
from math import ceil
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

app = Flask(__name__)

# Configuration
HOME_FOLDER = os.path.expanduser('~')
PORT = 5000
FILES_PER_PAGE = 20
SUPPORTED_EXTENSIONS = {
    'pdf': '📄 PDF',
    'docx': '📝 Word',
    'xlsx': '📊 Excel',
    'zip': '🗜️ ZIP'
}
DEBOUNCE_DELAY = 300
CACHE_EXPIRY = 5

# State management
dark_mode = False
file_cache = []
last_cache_update = 0
cache_lock = threading.Lock()
app.last_cache_update = 0

# Global variables for thread management
observer = None
cache_thread = None
stop_event = threading.Event()

class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, app):
        self.app = app
        self.debounce_timers = {}
        super().__init__()
    
    def on_any_event(self, event):
        if not event.is_directory:
            ext = event.src_path.lower().split('.')[-1] if '.' in event.src_path else ''
            if ext in SUPPORTED_EXTENSIONS:
                with app.app_context():
                    app.last_cache_update = 0
                    print(f"File change detected: {event.src_path}")  # Debug logging
                
                if event.src_path in self.debounce_timers:
                    self.debounce_timers[event.src_path].cancel()
                
                timer = threading.Timer(0.3, self.trigger_update)
                self.debounce_timers[event.src_path] = timer
                timer.start()
    
    def trigger_update(self):
        with app.app_context():
            print("File change detected - forcing immediate cache update")
            app.last_cache_update = 0
            # Force cache update immediately
            global last_cache_update
            last_cache_update = 0

def get_zip_contents(zip_path):
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            return zip_ref.namelist()[:5]
    except:
        return None

def update_file_cache():
    global file_cache, last_cache_update
    previous_files = {f['full_path']: f for f in file_cache}
    
    while not stop_event.is_set():
        if time.time() - last_cache_update > CACHE_EXPIRY or app.last_cache_update == 0:
            start_time = time.time()
            new_cache = []
            files_processed = 0
            files_updated = 0
            current_files = set()
            
            try:
                # First pass: collect all files
                for root, _, files_in_dir in os.walk(HOME_FOLDER):
                    if stop_event.is_set():
                        break
                    for file in files_in_dir:
                        if '.' in file:
                            ext = file.lower().split('.')[-1]
                            if ext in SUPPORTED_EXTENSIONS:
                                full_path = os.path.join(root, file)
                                current_files.add(full_path)
                
                # Second pass: process files
                for full_path in current_files:
                    if stop_event.is_set():
                        break
                    try:
                        ext = full_path.lower().split('.')[-1]
                        stat = os.stat(full_path)
                        current_mtime = stat.st_mtime
                        files_processed += 1
                        
                        cached_file = previous_files.get(full_path)
                        
                        if cached_file and cached_file['modified'] == current_mtime:
                            new_cache.append(cached_file)
                        else:
                            files_updated += 1
                            rel_path = os.path.relpath(full_path, HOME_FOLDER)
                            web_path = rel_path.replace('\\', '/')
                            
                            new_cache.append({
                                'name': os.path.basename(full_path),
                                'path': web_path,
                                'full_path': full_path,
                                'size': f"{stat.st_size/1024:.1f} KB",
                                'modified': current_mtime,
                                'modified_str': datetime.fromtimestamp(current_mtime).strftime('%Y-%m-%d %H:%M'),
                                'folder': os.path.dirname(rel_path) or '/',
                                'type': ext,
                                'icon': SUPPORTED_EXTENSIONS.get(ext, '📄'),
                                'zip_contents': get_zip_contents(full_path) if ext == 'zip' else None
                            })
                            
                    except (PermissionError, FileNotFoundError):
                        continue
                
                new_cache.sort(key=lambda x: -x['modified'])
                
                with cache_lock:
                    file_cache = new_cache
                    last_cache_update = time.time()
                    app.last_cache_update = last_cache_update
                    print(f"Cache updated with {len(file_cache)} files")  # Debug logging
                
                print(f"Cache updated in {time.time()-start_time:.2f}s - Processed: {files_processed}, Updated: {files_updated}, Total: {len(file_cache)}")
                
            except Exception as e:
                print(f"Cache update error: {str(e)}")
        
        time.sleep(1)

def start_background_threads():
    global observer, cache_thread
    
    # Initialize and start file observer
    event_handler = FileChangeHandler(app)
    observer = Observer()
    observer.schedule(event_handler, HOME_FOLDER, recursive=True)
    observer.start()
    
    # Start cache updater thread
    cache_thread = threading.Thread(target=update_file_cache)
    cache_thread.daemon = True
    cache_thread.start()

def stop_background_threads():
    global observer, cache_thread
    
    # Signal threads to stop
    stop_event.set()
    
    # Stop the observer
    if observer:
        observer.stop()
        observer.join()
    
    # Wait for cache thread to finish
    if cache_thread:
        cache_thread.join(timeout=2)
    
    print("Background threads stopped")

@app.route('/')
def index():
    search_query = request.args.get('search', '').strip().lower()
    file_type = request.args.get('type', 'all')
    page = int(request.args.get('page', 1))
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    # Remove the initial loading page since we're doing background updates
    if not file_cache:
        return render_template_string('''
            <!DOCTYPE html>
            <html>
            <head><title>Loading...</title></head>
            <body>
                <div style="text-align: center; padding: 2rem;">
                    <h2>Building initial file index...</h2>
                    <p>This may take a minute for large collections</p>
                </div>
                <script>
                    // Check every 5 seconds if we have data
                    function checkData() {
                        fetch('/has_data').then(response => response.json())
                            .then(data => {
                                if (data.has_data) {
                                    location.reload();
                                } else {
                                    setTimeout(checkData, 5000);
                                }
                            });
                    }
                    setTimeout(checkData, 5000);
                </script>
            </body>
            </html>
        ''')
    
    filtered_files = []
    with cache_lock:
        for file in file_cache:
            if file_type != 'all' and file['type'] != file_type:
                continue
                
            # Date range filtering
            if date_from or date_to:
                file_date = datetime.fromtimestamp(file['modified']).date()
                if date_from:
                    from_date = datetime.strptime(date_from, '%Y-%m-%d').date()
                    if file_date < from_date:
                        continue
                if date_to:
                    to_date = datetime.strptime(date_to, '%Y-%m-%d').date()
                    if file_date > to_date:
                        continue
                
            if search_query:
                search_terms = search_query.split()
                match = any(
                    term in file['name'].lower() or 
                    term in file['folder'].lower()
                    for term in search_terms
                )
                if not match:
                    continue
                    
            filtered_files.append(file)
    
    total_files = len(filtered_files)
    total_pages = ceil(total_files / FILES_PER_PAGE) if FILES_PER_PAGE > 0 else 1
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * FILES_PER_PAGE
    end_idx = start_idx + FILES_PER_PAGE
    paginated_files = filtered_files[start_idx:end_idx]
    showing_end = end_idx if end_idx <= total_files else total_files
    
    return render_template_string('''
        <!DOCTYPE html>
        <html lang="en" class="{{ 'dark' if dark_mode else '' }}">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>DPWH Sub - DEO | Records Management Unit</title>
            <link rel="icon" href="{{ url_for('favicon_png') }}">
            <style>
                                  
                body {
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 
                    Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif;
                }
                                  
                :root {
                    --primary: #4361ee;
                    --primary-light: #e6e9ff;
                    --text: #2b2d42;
                    --text-light: #8d99ae;
                    --bg: #f8f9fa;
                    --card-bg: #ffffff;
                }
                
                .logo-container {
                    display: flex;
                    align-items: center;
                    gap: 1rem;
                }
                
                .logo {
                    width: 40px;
                    height: 40px;
                    background-color: var(--primary);
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    color: white;
                    font-size: 1.5rem;
                    font-weight: bold;
                }
                
                .header-text {
                    display: flex;
                    flex-direction: column;
                }
                
                .dark {
                    --primary: #7c9eff;
                    --primary-light: #2d3748;
                    --text: #f7fafc;
                    --text-light: #a0aec0;
                    --bg: #1a202c;
                    --card-bg: #2d3748;
                }
                
                * {
                    box-sizing: border-box;
                    margin: 0;
                    padding: 0;
                }
                
                body {
                    font-family: 'Inter', sans-serif;
                    background-color: var(--bg);
                    color: var(--text);
                    line-height: 1.6;
                    padding: 2rem 1rem;
                    transition: background-color 0.3s, color 0.3s;
                }
                
                .container {
                    max-width: 1200px;
                    margin: 0 auto;
                }
                
                header {
                    margin-bottom: 2rem;
                }
                
                h1 {
                    font-size: 2rem;
                    font-weight: 600;
                    margin-bottom: 0.5rem;
                    color: var(--primary);
                }
                
                .subtitle {
                    color: var(--text-light);
                    margin-bottom: 1.5rem;
                }
                
                .search-container {
                    background: var(--card-bg);
                    border-radius: 8px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    padding: 1.5rem;
                    margin-bottom: 2rem;
                    transition: all 0.3s;
                }
                
                .search-box {
                    display: flex;
                    gap: 0.5rem;
                    margin-bottom: 1rem;
                }
                
                .search-box button {
                    padding: 0 1rem;
                }
                
                .date-range-box {
                    display: flex;
                    gap: 0.5rem;
                    margin-bottom: 1rem;
                    align-items: center;
                }
                
                .date-range-box input[type="date"] {
                    background: var(--card-bg);
                    color: var(--text);
                    border: 1px solid #ddd;
                    font-family: 'Inter', sans-serif;
                    padding: 0.5rem;
                    border-radius: 6px;
                    transition: border 0.2s;
                }
                
                .date-range-box input[type="date"]:focus {
                    outline: none;
                    border-color: var(--primary);
                }
                
                .date-reset-btn {
                    background-color: #f8f9fa;
                    color: #6c757d;
                    border: 1px solid #ddd;
                    padding: 0.5rem 1rem;
                    border-radius: 6px;
                    font-size: 0.9rem;
                    cursor: pointer;
                    transition: all 0.2s;
                    margin-left: 0.5rem;
                }
                
                .date-reset-btn:hover {
                    background-color: #e9ecef;
                    color: #495057;
                }
                
                .dark .date-range-box input[type="date"] {
                    background: var(--card-bg);
                    color: var(--text);
                }
                
                .dark .date-reset-btn {
                    background-color: #2d3748;
                    color: #a0aec0;
                    border-color: #4a5568;
                }
                
                .dark .date-reset-btn:hover {
                    background-color: #4a5568;
                    color: #f7fafc;
                }
                
                .filter-box {
                    display: flex;
                    gap: 0.5rem;
                    flex-wrap: wrap;
                }
                
                .filter-btn {
                    background-color: var(--card-bg);
                    color: var(--text);
                    border: 1px solid #ddd;
                    padding: 0.5rem 1rem;
                    border-radius: 6px;
                    font-size: 0.9rem;
                    cursor: pointer;
                    transition: all 0.2s;
                }
                
                .filter-btn:hover {
                    border-color: var(--primary);
                }
                
                .filter-btn.active {
                    background-color: var(--primary);
                    color: white;
                    border-color: var(--primary);
                }
                
                input[type="text"] {
                    flex: 1;
                    padding: 0.75rem 1rem;
                    border: 1px solid #ddd;
                    border-radius: 6px;
                    font-size: 1rem;
                    transition: border 0.2s;
                    background: var(--card-bg);
                    color: var(--text);
                }
                
                input[type="text"]:focus {
                    outline: none;
                    border-color: var(--primary);
                }
                
                button {
                    background-color: var(--primary);
                    color: white;
                    border: none;
                    padding: 0 1.5rem;
                    border-radius: 6px;
                    font-weight: 500;
                    cursor: pointer;
                    transition: background 0.2s;
                }
                
                button:hover {
                    background-color: #3a56d4;
                }
                
                .results-count {
                    color: var(--text-light);
                    font-size: 0.9rem;
                    margin-top: 1rem;
                }
                
                .file-list {
                    display: grid;
                    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
                    gap: 1rem;
                    margin-bottom: 2rem;
                    transition: opacity 0.3s ease;
                }
                
                .file-card {
                    background: var(--card-bg);
                    border-radius: 8px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                    padding: 1.5rem;
                    transition: transform 0.2s, box-shadow 0.2s;
                }
                
                .file-card:hover {
                    transform: translateY(-2px);
                    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
                }
                
                .file-name {
                    font-weight: 600;
                    margin-bottom: 0.5rem;
                    color: var(--primary);
                    text-decoration: none;
                    display: block;
                }
                
                .file-icon {
                    font-size: 1.2rem;
                    margin-right: 0.5rem;
                }
                
                .file-meta {
                    font-size: 0.85rem;
                    color: var(--text-light);
                }
                
                .file-meta div {
                    margin-bottom: 0.25rem;
                }
                
                .file-type {
                    display: inline-block;
                    padding: 0.2rem 0.5rem;
                    background-color: var(--primary-light);
                    color: var(--primary);
                    border-radius: 4px;
                    font-size: 0.75rem;
                    margin-right: 0.5rem;
                }
                
                .folder {
                    display: flex;
                    align-items: center;
                    gap: 0.25rem;
                    margin-top: 0.5rem;
                    padding-top: 0.5rem;
                    border-top: 1px solid #eee;
                }
                
                .no-results {
                    text-align: center;
                    padding: 2rem;
                    color: var(--text-light);
                    grid-column: 1 / -1;
                }
                
                .pagination {
                    display: flex;
                    justify-content: center;
                    gap: 0.5rem;
                    margin-top: 2rem;
                }
                
                .page-link {
                    padding: 0.5rem 1rem;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    text-decoration: none;
                    color: var(--text);
                    transition: all 0.2s;
                }
                
                .page-link:hover {
                    background-color: var(--primary-light);
                    border-color: var(--primary);
                }
                
                .page-link.active {
                    background-color: var(--primary);
                    color: white;
                    border-color: var(--primary);
                }
                
                .page-link.disabled {
                    opacity: 0.5;
                    pointer-events: none;
                }
                
                .zip-contents {
                    font-size: 0.8rem;
                    color: var(--text-light);
                    margin-top: 0.5rem;
                }
                
                .dark-mode-toggle {
                    background: var(--primary-light);
                    border: 1px solid var(--primary);
                    padding: 0.4rem 1rem;
                    border-radius: 20px;
                    cursor: pointer;
                    font-size: 0.9rem;
                    transition: all 0.3s ease;
                    display: flex;
                    align-items: center;
                    gap: 0.5rem;
                    color: var(--primary);
                    font-weight: 500;
                }
                
                .dark-mode-toggle:hover {
                    background: var(--primary);
                    color: white;
                    transform: scale(1.05);
                    box-shadow: 0 2px 8px rgba(67, 97, 238, 0.3);
                }
                
                .dark .dark-mode-toggle {
                    background: rgba(124, 158, 255, 0.1);
                    border-color: var(--primary);
                    color: var(--primary);
                }
                
                .dark .dark-mode-toggle:hover {
                    background: var(--primary);
                    color: white;
                    box-shadow: 0 2px 12px rgba(124, 158, 255, 0.4);
                }
                                  
                .pagination {
                    display: flex;
                    justify-content: center;
                    gap: 0.25rem;
                    margin-top: 2rem;
                    flex-wrap: wrap;
                }

                .page-link {
                    padding: 0.5rem 0.75rem;
                    min-width: 2.5rem;
                    text-align: center;
                    border: 1px solid #ddd;
                    border-radius: 4px;
                    text-decoration: none;
                    color: var(--text);
                    transition: all 0.2s;
                }

                .page-link:hover {
                    background-color: var(--primary-light);
                    border-color: var(--primary);
                }

                .page-link.active {
                    background-color: var(--primary);
                    color: white;
                    border-color: var(--primary);
                }

                .page-link.disabled {
                    opacity: 0.5;
                    pointer-events: none;
                    background-color: var(--card-bg);
                }

                .loading-indicator {
                    position: fixed;
                    bottom: 20px;
                    right: 20px;
                    background: var(--primary);
                    color: white;
                    padding: 8px 16px;
                    border-radius: 20px;
                    z-index: 1000;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.2);
                    transition: all 0.3s ease;
                }

                @media (max-width: 768px) {
                    .pagination {
                        gap: 0.1rem;
                    }
                    .page-link {
                        padding: 0.3rem 0.5rem;
                        min-width: 2rem;
                    }
                }
                
                @media (max-width: 768px) {
                    .file-list {
                        grid-template-columns: 1fr;
                    }
                    
                    .pagination {
                        flex-wrap: wrap;
                    }
                    
                    .filter-box {
                        justify-content: center;
                    }
                    
                    .date-range-box {
                        flex-direction: column;
                        align-items: flex-start;
                    }
                    
                    .search-box {
                        flex-direction: column;
                    }
                    
                    .search-box button {
                        width: 100%;
                        margin-top: 0.5rem;
                    }
                }
            </style>
        </head>
        <body>
            <div class="container">
                <header>
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div class="logo-container">
                            <div class="header-text">
                                <h1>DPWH Sub - DEO | Records Management Unit</h1>
                                <p class="subtitle">Search files by name, folder, date, or type</p>
                            </div>
                        </div>
            <button onclick="toggleDarkMode()" class="dark-mode-toggle">
    {{ '☀️ Light Mode' if dark_mode else '🌙 Dark Mode' }}
</button>
                    </div>
                </header>
                
                <div class="search-container">
                    <div class="search-box">
                        <input type="text" id="search-input" placeholder="Automatically search files, date, or folders..." 
                               value="{{ search_query }}" autofocus>
                                   <button id="search-button">Search</button>
                    </div>
                    <div class="date-range-box">
                        <input type="date" id="date-from" name="date_from" value="{{ date_from }}"
                               placeholder="From date">
                        <span>to</span>
                        <input type="date" id="date-to" name="date_to" value="{{ date_to }}"
                               placeholder="To date">
                    <button onclick="resetDates()" class="date-reset-btn" type="button">
                        Reset
                    </button>
                    </div>
 <div class="filter-box">
    <a href="javascript:void(0)" onclick="setFileType('all')" 
       class="filter-btn {% if file_type == 'all' %}active{% endif %}">All Files</a>
    {% for ext, label in SUPPORTED_EXTENSIONS.items() %}
        <a href="javascript:void(0)" onclick="setFileType('{{ ext }}')" 
           class="filter-btn {% if file_type == ext %}active{% endif %}">
            {{ label }}
        </a>
    {% endfor %}
</div>
                    <div class="results-count">
                        Found {{ total_files }} file{% if total_files != 1 %}s{% endif %}
                        {% if file_type != 'all' %} ({{ SUPPORTED_EXTENSIONS.get(file_type, '') }}){% endif %}
                        {% if search_query %} matching "{{ search_query }}"{% endif %}
                        {% if date_from or date_to %} modified between {{ date_from }} and {{ date_to }}{% endif %}
                        (Showing {{ start_idx + 1 }}-{{ showing_end }})
                    </div>
                </div>
                
                <div class="file-list" id="file-list-container">
                    {% if not paginated_files %}
                        <div class="no-results">
                            <p>No files found{% if search_query %} matching "{{ search_query }}"{% endif %}</p>
                            {% if date_from or date_to %}
                                <p>modified between {{ date_from }} and {{ date_to }}</p>
                            {% endif %}
                            <p>Try a different search term, file type, or date range</p>
                        </div>
                    {% else %}
                        {% for file in paginated_files %}
                            <div class="file-card" data-file-path="{{ file.path }}">
                                <a href="/file/{{ file.path }}" class="file-name" target="_blank">
                                    <span class="file-icon">{{ file.icon }}</span>{{ file.name }}
                                </a>
                                <div class="file-meta">
                                    <div>
                                        <span class="file-type">{{ file.type|upper }}</span>
                                        {{ file.size }}
                                    </div>
                                    <div>Modified: {{ file.modified_str }}</div>
                                    {% if file.zip_contents %}
                                        <div class="zip-contents">
                                            Contains: {{ file.zip_contents|join(', ') }}{% if file.zip_contents|length >= 5 %}...{% endif %}
                                        </div>
                                    {% endif %}
                                    <div class="folder">
                                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2z"></path>
                                        </svg>
                                        {{ file.folder }}
                                    </div>
                                </div>
                            </div>
                        {% endfor %}
                    {% endif %}
                </div>
                
                {% if total_pages > 1 %}
                <div class="pagination">
                    {% if page > 1 %}
                        <a href="?search={{ search_query }}&type={{ file_type }}&page=1&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">First</a>
                        <a href="?search={{ search_query }}&type={{ file_type }}&page={{ page - 1 }}&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">Previous</a>
                    {% else %}
                        <span class="page-link disabled">First</span>
                        <span class="page-link disabled">Previous</span>
                    {% endif %}

                    {# Always show first page #}
                    {% if page > 3 %}
                        <a href="?search={{ search_query }}&type={{ file_type }}&page=1&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">1</a>
                        {% if page > 4 %}
                            <span class="page-link disabled">...</span>
                        {% endif %}
                    {% endif %}

                    {# Show pages around current page #}
                    {% for p in range([1, page-2]|max, [page+3, total_pages + 1]|min) %}
                        {% if p == page %}
                            <span class="page-link active">{{ p }}</span>
                        {% else %}
                            <a href="?search={{ search_query }}&type={{ file_type }}&page={{ p }}&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">{{ p }}</a>
                        {% endif %}
                    {% endfor %}

                    {# Always show last page #}
                    {% if page < total_pages - 2 %}
                        {% if page < total_pages - 3 %}
                            <span class="page-link disabled">...</span>
                        {% endif %}
                        <a href="?search={{ search_query }}&type={{ file_type }}&page={{ total_pages }}&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">{{ total_pages }}</a>
                    {% endif %}

                    {% if page < total_pages %}
                        <a href="?search={{ search_query }}&type={{ file_type }}&page={{ page + 1 }}&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">Next</a>
                        <a href="?search={{ search_query }}&type={{ file_type }}&page={{ total_pages }}&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">Last</a>
                    {% else %}
                        <span class="page-link disabled">Next</span>
                        <span class="page-link disabled">Last</span>
                    {% endif %}
                </div>
                {% endif %}
            </div>

          <script>
    // Global variables
    let performSearch; // Declare the function variable globally

    // Global functions
    function resetDates() {
        const dateFromInput = document.getElementById('date-from');
        const dateToInput = document.getElementById('date-to');
        if (dateFromInput && dateToInput) {
            dateFromInput.value = '';
            dateToInput.value = '';
            if (typeof performSearch === 'function') {
                performSearch();
            }
        }
    }

    function setFileType(type) {
        const dateFromInput = document.getElementById('date-from');
        const dateToInput = document.getElementById('date-to');
        const searchInput = document.getElementById('search-input');
        if (dateFromInput && dateToInput && searchInput) {
            const dateFrom = dateFromInput.value;
            const dateTo = dateToInput.value;
            const searchQuery = searchInput.value;
            
            // Get current page from URL or default to current page
            const urlParams = new URLSearchParams(window.location.search);
            const currentPage = urlParams.get('page') || {{ page }};
            
            let url = `?search=${encodeURIComponent(searchQuery)}&type=${type}&page=${currentPage}`;
            if (dateFrom) url += `&date_from=${dateFrom}`;
            if (dateTo) url += `&date_to=${dateTo}`;
            
            window.location.href = url;
        }
    }

    function toggleDarkMode() {
        fetch('/toggle_dark_mode').then(() => location.reload());
    }

    // Wait for DOM to be fully loaded before executing JavaScript
    document.addEventListener('DOMContentLoaded', function() {
        // ⚡ Instant Search (Debounced Typing)
        let searchTimer;
        const searchInput = document.getElementById('search-input');
        const dateFromInput = document.getElementById('date-from');
        const dateToInput = document.getElementById('date-to');
        const searchButton = document.getElementById('search-button');
        const fileListContainer = document.getElementById('file-list-container');
        let lastTypingTime = 0;
        
        // Only proceed if all required elements exist
        if (searchInput && dateFromInput && dateToInput && searchButton && fileListContainer) {
            // Define performSearch function and assign it to the global variable
            performSearch = function() {
                const searchQuery = searchInput.value.trim().toLowerCase();
                const dateFrom = dateFromInput.value;
                const dateTo = dateToInput.value;
                const fileType = '{{ file_type }}';
                
                // Get current page from URL or default to current page
                const urlParams = new URLSearchParams(window.location.search);
                const currentPage = urlParams.get('page') || {{ page }};
                
                // Show subtle loading indicator
                const loadingIndicator = document.createElement('div');
                loadingIndicator.className = 'loading-indicator';
                loadingIndicator.style = 'position: fixed; bottom: 20px; right: 20px; background: var(--primary); color: white; padding: 8px 16px; border-radius: 20px; z-index: 1000;';
                loadingIndicator.textContent = 'Updating results...';
                document.body.appendChild(loadingIndicator);
                
                // Build URL with current parameters including page
                let url = `?search=${encodeURIComponent(searchQuery)}&type=${fileType}&page=${currentPage}`;
                if (dateFrom) url += `&date_from=${dateFrom}`;
                if (dateTo) url += `&date_to=${dateTo}`;
                
                // Use fetch API to get updated results without full page reload
                fetch(url)
                    .then(response => response.text())
                    .then(html => {
                        // Parse the HTML response
                        const parser = new DOMParser();
                        const doc = parser.parseFromString(html, 'text/html');
                        const newContent = doc.querySelector('.file-list').innerHTML;
                        const newPagination = doc.querySelector('.pagination')?.innerHTML || '';
                        const newResultsCount = doc.querySelector('.results-count').innerHTML;
                        
                        // Update the page content smoothly
                        fileListContainer.style.opacity = '0.8';
                        setTimeout(() => {
                            fileListContainer.innerHTML = newContent;
                            fileListContainer.style.opacity = '1';
                            if (newPagination) {
                                const paginationDiv = document.querySelector('.pagination');
                                if (paginationDiv) {
                                    paginationDiv.innerHTML = newPagination;
                                }
                            }
                            document.querySelector('.results-count').innerHTML = newResultsCount;
                            loadingIndicator.textContent = 'Updated!';
                            setTimeout(() => loadingIndicator.remove(), 1000);
                            
                            // Update browser history to maintain current page
                            window.history.pushState({}, '', url);
                        }, 200);
                    })
                    .catch(error => {
                        console.error('Search error:', error);
                        loadingIndicator.textContent = 'Update failed';
                        setTimeout(() => loadingIndicator.remove(), 2000);
                    });
            };
            
            // Set up event listeners with optimized debouncing
            searchInput.addEventListener('input', function() {
                lastTypingTime = Date.now();
                clearTimeout(searchTimer);
                searchTimer = setTimeout(performSearch, {{ DEBOUNCE_DELAY }});
            });
            
            searchButton.addEventListener('click', performSearch);
            
            // Add change listeners for date inputs
            dateFromInput.addEventListener('change', performSearch);
            dateToInput.addEventListener('change', performSearch);
            
            // 🔄 Real-time updates with Server-Sent Events
            const eventSource = new EventSource('/updates');
            eventSource.onmessage = function(e) {
                console.log('File system update detected');
                // Only refresh if we're not currently typing
                if (!searchInput.value || Date.now() - lastTypingTime > 5000) {
                    performSearch();
                }
            };
                                  
            // Add popstate event listener to handle browser back/forward navigation
            window.addEventListener('popstate', function() {
                performSearch();
            });
        }
    });
</script>
        </body>
        </html>
    ''', 
    search_query=search_query,
    file_type=file_type,
    paginated_files=paginated_files,
    total_files=total_files,
    total_pages=total_pages,
    page=page,
    start_idx=start_idx,
    showing_end=showing_end,
    SUPPORTED_EXTENSIONS=SUPPORTED_EXTENSIONS,
    dark_mode=dark_mode,
    date_from=date_from,
    date_to=date_to,
    DEBOUNCE_DELAY=DEBOUNCE_DELAY)

# API Endpoints
@app.route('/toggle_dark_mode')
def toggle_dark_mode():
    global dark_mode
    dark_mode = not dark_mode
    return jsonify({"dark_mode": dark_mode})

@app.route('/has_data')
def has_data():
    return jsonify({'has_data': len(file_cache) > 0})

@app.route('/updates')
def updates():
    def event_stream():
        last_version = app.last_cache_update
        last_file_count = len(file_cache)
        while True:
            # Check if cache was updated or file count changed significantly
            current_file_count = len(file_cache)
            if app.last_cache_update > last_version or abs(current_file_count - last_file_count) > 5:
                last_version = app.last_cache_update
                last_file_count = current_file_count
                yield f"data: {last_version}\n\n"
            time.sleep(1)  # Check more frequently but only send updates when needed
    
    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/file/<path:filename>')
def serve_file(filename):
    return send_from_directory(HOME_FOLDER, filename)

@app.route('/favicon.png')
def favicon_png():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                             'DPWH.png', mimetype='image/png')

if __name__ == '__main__':
    try:
        start_background_threads()
        
        url = f'http://localhost:{PORT}'
        print(f"File Finder Pro running at {url}")
        print("Building initial file cache...")
        webbrowser.open(url)
        
        app.run(port=PORT, threaded=True)
        
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    except Exception as e:
        print(f"Error: {str(e)}")
    finally:
        stop_background_threads()
        print("Cleanup complete. You can now safely close VS Code.")