# Granby Smart Canteen

All-features school canteen ordering system.

## Included
- Student menu search/filter/sort
- Favorites and reorder
- Cash / GCash checkout
- GCash reference number
- Order-success page + QR + printable receipt
- Student order history/profile/notifications
- Admin dashboard without main navbar
- Products, inventory, users, reports, settings
- Best customers, best sellers, popular categories, repeat customers
- Cash vs GCash analytics
- Admin new-order popup + sound + browser notification
- Database backup/restore
- Reset System
- Dark mode and workspace settings

## Run
```bash
py -m pip install -r requirements.txt
py app.py
```
Open http://127.0.0.1:5000

Default admin: admin@smartcanteen.local / admin123


## PWA / Install on Android
This version includes a Progressive Web App manifest, icons, and service worker.

1. Run the Flask app normally.
2. Open the site in Chrome on Android.
3. For PWA installation, the site should be served over HTTPS (localhost is also treated as secure for local development).
4. Use Chrome's **Install app** / **Add to Home screen** option.

The PWA does not require Node.js, Capacitor, or Android Studio. Note that the Flask server and database still need to run somewhere; installing the PWA does not turn Flask into a fully offline server.
