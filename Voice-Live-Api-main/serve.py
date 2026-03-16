#!/usr/bin/env python3
"""
Simple HTTP server for Azure Voice Live web frontend
Run this to serve the HTML/JS files locally
"""

import http.server
import socketserver
import webbrowser
import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv

def load_config():
    """Load configuration from .env file"""
    script_dir = Path(__file__).parent
    env_path = script_dir / '.env'
    
    if env_path.exists():
        load_dotenv(env_path)
        endpoint = os.getenv('AZURE_ENDPOINT', '')
        api_key = os.getenv('AZURE_API_KEY', '')
        return {
            'endpoint': endpoint,
            'apiKey': api_key
        }
    else:
        print("⚠️  Warning: .env file not found. Please create it with AZURE_ENDPOINT and AZURE_API_KEY")
        return {
            'endpoint': '',
            'apiKey': ''
        }

def main():
    # Change to the directory containing this script
    script_dir = Path(__file__).parent
    os.chdir(script_dir)
    
    # Load configuration from .env
    config = load_config()
    
    PORT = 5003
    
    class CustomHandler(http.server.SimpleHTTPRequestHandler):
        def end_headers(self):
            # Add CORS headers for local development
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', '*')
            # Required for audio worklets
            self.send_header('Cross-Origin-Embedder-Policy', 'require-corp')
            self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
            super().end_headers()
        
        def do_GET(self):
            # Handle config endpoint
            if self.path == '/config':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(config).encode())
                return
            
            # Handle index.html - inject config
            if self.path == '/' or self.path == '/index.html':
                try:
                    html_path = script_dir / 'index.html'
                    if html_path.exists():
                        with open(html_path, 'r', encoding='utf-8') as f:
                            html_content = f.read()
                        
                        # Inject config as JavaScript variable
                        config_script = f'''
    <script>
        window.AZURE_CONFIG = {json.dumps(config)};
    </script>
'''
                        # Insert before closing </head> tag
                        html_content = html_content.replace('</head>', config_script + '</head>')
                        
                        self.send_response(200)
                        self.send_header('Content-type', 'text/html; charset=utf-8')
                        self.end_headers()
                        self.wfile.write(html_content.encode('utf-8'))
                        return
                except Exception as e:
                    print(f"Error serving index.html: {e}")
            
            # Serve other files normally
            return super().do_GET()
    
    try:
        with socketserver.TCPServer(("", PORT), CustomHandler) as httpd:
            print(f"🌐 Azure Voice Live Web Frontend")
            print(f"📡 Server running at http://localhost:{PORT}")
            print(f"📁 Serving files from: {script_dir}")
            print(f"🔗 Opening browser...")
            print(f"📝 Press Ctrl+C to stop the server")
            print()
            
            # Open browser automatically
            webbrowser.open(f'http://localhost:{PORT}')
            
            print("🎤 Ready to use Azure Voice Live Chat!")
            httpd.serve_forever()
            
    except KeyboardInterrupt:
        print("\n👋 Server stopped.")
    except OSError as e:
        if e.errno == 48:  # Port already in use
            print(f"❌ Port {PORT} is already in use.")
            print(f"💡 Try a different port or stop the existing server.")
        else:
            print(f"❌ Error starting server: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
