import os
import time
import threading
import zipfile
from flask import Flask, render_template_string, request, send_from_directory, jsonify
import webbrowser
from datetime import datetime
from math import ceil

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
DEBOUNCE_DELAY = 500  # milliseconds for instant search
CACHE_EXPIRY = 30     # seconds for auto-refresh

# State management
dark_mode = False
file_cache = []
last_cache_update = 0
cache_lock = threading.Lock()

def get_zip_contents(zip_path):
    """Get first 5 files from a ZIP archive"""
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            return zip_ref.namelist()[:5]
    except:
        return None

def update_file_cache():
    """Background thread to maintain file cache"""
    global file_cache, last_cache_update
    while True:
        start_time = time.time()
        new_cache = []
        
        try:
            for root, _, files_in_dir in os.walk(HOME_FOLDER):
                for file in files_in_dir:
                    if '.' in file:
                        ext = file.lower().split('.')[-1]
                        if ext in SUPPORTED_EXTENSIONS:
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, HOME_FOLDER)
                            web_path = rel_path.replace('\\', '/')
                            
                            try:
                                stat = os.stat(full_path)
                                
                                new_cache.append({
                                    'name': file,
                                    'path': web_path,
                                    'full_path': full_path,
                                    'size': f"{stat.st_size/1024:.1f} KB",
                                    'modified': stat.st_mtime,
                                    'modified_str': datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M'),
                                    'folder': os.path.dirname(rel_path) or '/',
                                    'type': ext,
                                    'icon': SUPPORTED_EXTENSIONS.get(ext, '📄'),
                                    'zip_contents': get_zip_contents(full_path) if ext == 'zip' else None
                                })
                            except (PermissionError, FileNotFoundError):
                                continue
            
            # Sort by modified date (newest first)
            new_cache.sort(key=lambda x: -x['modified'])
            
            with cache_lock:
                file_cache = new_cache
                last_cache_update = time.time()
                
            print(f"Cache updated in {time.time()-start_time:.2f}s with {len(file_cache)} files")
            
        except Exception as e:
            print(f"Cache update error: {str(e)}")
        
        time.sleep(CACHE_EXPIRY)

# Start background cache updater
cache_thread = threading.Thread(target=update_file_cache, daemon=True)
cache_thread.start()

@app.route('/')
def index():
    search_query = request.args.get('search', '').strip().lower()
    file_type = request.args.get('type', 'all')
    page = int(request.args.get('page', 1))
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    
    if not file_cache and time.time() - last_cache_update > 5:
        return render_template_string('''
            <!DOCTYPE html>
            <html>
            <head><title>Loading...</title></head>
            <body>
                <div style="text-align: center; padding: 2rem;">
                    <h2>Building file index...</h2>
                    <p>This may take a minute for large collections</p>
                    <p>Page will refresh automatically</p>
                    <p>Do not interrupt</p>
                </div>
                <script>
                    setTimeout(() => location.reload(), 2000);
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
            <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap" rel="stylesheet">
            <style>
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
                
                <div class="file-list">
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
                            <div class="file-card">
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
                        <a href="?search={{ search_query }}&type={{ file_type }}&page={{ page - 1 }}&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">Previous</a>
                    {% else %}
                        <span class="page-link disabled">Previous</span>
                    {% endif %}
                    
                    {% for p in range(1, total_pages + 1) %}
                        {% if p == page %}
                            <span class="page-link active">{{ p }}</span>
                        {% else %}
                            <a href="?search={{ search_query }}&type={{ file_type }}&page={{ p }}&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">{{ p }}</a>
                        {% endif %}
                    {% endfor %}
                    
                    {% if page < total_pages %}
                        <a href="?search={{ search_query }}&type={{ file_type }}&page={{ page + 1 }}&date_from={{ date_from }}&date_to={{ date_to }}" class="page-link">Next</a>
                    {% else %}
                        <span class="page-link disabled">Next</span>
                    {% endif %}
                </div>
                {% endif %}
            </div>

                       <script>
                // ⚡ Instant Search (Debounced Typing)
                let searchTimer;
                const searchInput = document.getElementById('search-input');
                const dateFromInput = document.getElementById('date-from');
                const dateToInput = document.getElementById('date-to');
                
                // Set up event listeners
                searchInput.addEventListener('input', function() {
                    clearTimeout(searchTimer);
                    searchTimer = setTimeout(() => {
                        performSearch();
                    }, {{ DEBOUNCE_DELAY }});
                });
                
                // Add change listeners for date inputs
                dateFromInput.addEventListener('change', performSearch);
                dateToInput.addEventListener('change', performSearch);
                
                function performSearch() {
                    const searchQuery = searchInput.value;
                    const dateFrom = dateFromInput.value;
                    const dateTo = dateToInput.value;
                    
                    let url = `?search=${encodeURIComponent(searchQuery)}&type={{ file_type }}&page=1`;
                    if (dateFrom) url += `&date_from=${dateFrom}`;
                    if (dateTo) url += `&date_to=${dateTo}`;
                    
                    window.location.href = url;
                }
                
                function setFileType(type) {
                    const dateFrom = dateFromInput.value;
                    const dateTo = dateToInput.value;
                    
                    let url = `?search={{ search_query }}&type=${type}&page=1`;
                    if (dateFrom) url += `&date_from=${dateFrom}`;
                    if (dateTo) url += `&date_to=${dateTo}`;
                    
                    window.location.href = url;
                }
                
                // Date range reset
                function resetDates() {
                    dateFromInput.value = '';
                    dateToInput.value = '';
                    performSearch();
                }
                
                // 🎨 Dark Mode Toggle
                function toggleDarkMode() {
                    fetch('/toggle_dark_mode').then(() => location.reload());
                }
                
                // 🔄 Background Auto-Refresh
                setInterval(() => {
                    fetch('/check_refresh').then(res => res.json()).then(data => {
                        if (data.needs_refresh) {
                            location.reload();
                        }
                    });
                }, 30000);
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

@app.route('/check_refresh')
def check_refresh():
    global last_cache_update
    needs_refresh = (time.time() - last_cache_update) > CACHE_EXPIRY
    return jsonify({"needs_refresh": needs_refresh})

@app.route('/file/<path:filename>')
def serve_file(filename):
    return send_from_directory(HOME_FOLDER, filename)

if __name__ == '__main__':
    url = f'http://localhost:{PORT}'
    print(f"File Finder Pro running at {url}")
    print("Building initial file cache...")
    webbrowser.open(url)
    app.run(port=PORT, threaded=True)