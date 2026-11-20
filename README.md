# TraceHub – Smart College Lost & Found
Python (standard library only) backend + single-page browser UI. No installs needed.

    python3 server.py          # then open http://localhost:8000
    PORT=9000 python3 server.py

Demo logins: aarav@tracehub.edu / demo123 (also meena@, karthik@) · admin@tracehub.edu / admin123
Delete tracehub.db to reset the data.

Features: register/login, report lost/found with photo, search & filters, AI match ranking
(TF-IDF text + campus distance + time, with category/date gates), match notifications,
secret-detail owner verification, 6-digit OTP handover, reward points, leaderboard, campus hotspots.
