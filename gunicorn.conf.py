import os

# Render automatically provides $PORT environment variable
port = os.environ.get("PORT", "10000")
bind = f"0.0.0.0:{port}"

workers = 2
threads = 4
timeout = 120
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"
