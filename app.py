import os
import uuid
import csv
import io
import shutil
from functools import wraps
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory, send_file, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import qrcode

from models import db, User, Product, Order, OrderItem, Notification, ActivityLog, AppSetting

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "change-this-secret-key")
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql+psycopg2://" + DATABASE_URL[len("postgres://"):]

# Render: use DATABASE_URL when provided (recommended for production).
# Otherwise fall back to SQLite. DATA_DIR can point to a persistent disk such as /var/data.
if DATABASE_URL:
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
    os.makedirs(DATA_DIR, exist_ok=True)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(DATA_DIR, "smart_canteen.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

def current_user():
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None

@app.context_processor
def inject_globals():
    cart = session.get("cart", {})
    cart_count = sum(int(qty) for qty in cart.values())
    user = current_user()
    unread = Notification.query.filter_by(user_id=user.id, is_read=False).count() if user else 0
    return {
        "current_user": user,
        "cart_count": cart_count,
        "unread_notifications": unread
    }

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        if user.role != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped

def create_notification(user_id, title, message):
    db.session.add(Notification(
        user_id=user_id,
        title=title,
        message=message
    ))

def generate_order_qr(order_number):
    folder = os.path.join(BASE_DIR, "qr_codes")
    os.makedirs(folder, exist_ok=True)
    filename = f"{order_number}.png"
    path = os.path.join(folder, filename)
    img = qrcode.make(order_number)
    img.save(path)
    return filename

def log_activity(action, user_id=None):
    db.session.add(ActivityLog(user_id=user_id if user_id is not None else (current_user().id if current_user() else None), action=action))

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def save_product_image(file):
    if not file or not file.filename:
        return ""
    if not allowed_file(file.filename):
        return ""
    filename = f"{uuid.uuid4().hex[:12]}_{secure_filename(file.filename)}"
    file.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    return url_for("static", filename=f"uploads/{filename}")

def migrate_database():
    # Lightweight SQLite migration so existing school-project databases keep working.
    inspector = db.inspect(db.engine)
    columns = {c["name"] for c in inspector.get_columns("user")}
    for name, sql_type in [("phone", "VARCHAR(40)"), ("grade_section", "VARCHAR(100)"), ("avatar", "VARCHAR(255)"), ("is_active", "BOOLEAN DEFAULT 1")]:
        if name not in columns:
            db.session.execute(db.text(f"ALTER TABLE user ADD COLUMN {name} {sql_type} DEFAULT ''"))
    order_columns = {c["name"] for c in inspector.get_columns("order")}
    if "payment_reference" not in order_columns:
        db.session.execute(db.text("ALTER TABLE 'order' ADD COLUMN payment_reference VARCHAR(100) DEFAULT ''"))
    db.session.commit()

def setting_value(key, default=""):
    item = AppSetting.query.filter_by(key=key).first()
    return item.value if item else default

def set_setting(key, value):
    item = AppSetting.query.filter_by(key=key).first()
    if not item:
        item = AppSetting(key=key, value=str(value))
        db.session.add(item)
    else:
        item.value = str(value)

def seed_database():
    if User.query.filter_by(email="admin@smartcanteen.local").first() is None:
        admin = User(
            name="Canteen Administrator",
            email="admin@smartcanteen.local",
            password_hash=generate_password_hash("admin123"),
            role="admin"
        )
        db.session.add(admin)

    if Product.query.count() == 0:
        products = [
            Product(name="Chicken Meal", description="Chicken with rice and gravy.",
                    price=65, category="Meals", stock=50, available=True),
            Product(name="Burger", description="Beef burger with lettuce and cheese.",
                    price=45, category="Snacks", stock=40, available=True),
            Product(name="French Fries", description="Crispy golden fries.",
                    price=35, category="Snacks", stock=50, available=True),
            Product(name="Siomai", description="Steamed pork siomai, 4 pieces.",
                    price=30, category="Snacks", stock=60, available=True),
            Product(name="Iced Tea", description="Refreshing iced tea.",
                    price=25, category="Drinks", stock=80, available=True),
            Product(name="Bottled Water", description="500ml bottled water.",
                    price=20, category="Drinks", stock=100, available=True),
        ]
        db.session.add_all(products)

    db.session.commit()

@app.route("/")
def index():
    products = Product.query.filter_by(available=True).order_by(Product.id.desc()).limit(6).all()
    return render_template("index.html", products=products)

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("student_dashboard"))

    if request.method == "POST":
        student_id = request.form.get("student_id", "").strip()
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")

        if not all([student_id, name, email, password]):
            flash("Please fill in all required fields.", "danger")
            return render_template("register.html")

        if password != confirm:
            flash("Passwords do not match.", "danger")
            return render_template("register.html")

        if User.query.filter((User.email == email) | (User.student_id == student_id)).first():
            flash("Email or Student ID is already registered.", "danger")
            return render_template("register.html")

        user = User(
            student_id=student_id,
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
            role="student"
        )
        db.session.add(user)
        db.session.commit()

        create_notification(user.id, "Welcome to Smart Canteen",
                             "Your account has been created successfully.")
        db.session.commit()

        flash("Registration successful. You can now log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("student_dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        user = User.query.filter_by(email=email).first()

        if user and user.is_active and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            session["cart"] = {}
            flash("Welcome back!", "success")
            if user.role == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("student_dashboard"))

        flash("Invalid email or password.", "danger")

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def student_dashboard():
    user = current_user()
    orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).limit(8).all()
    favorite_count = len(session.get("favorites", []))
    return render_template("student_dashboard.html", orders=orders, favorite_count=favorite_count)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        user.name = request.form.get("name", user.name).strip()
        user.phone = request.form.get("phone", "").strip()
        user.grade_section = request.form.get("grade_section", "").strip()
        if not user.name:
            flash("Name is required.", "danger")
            return render_template("profile.html")
        db.session.commit()
        flash("Profile updated successfully.", "success")
        return redirect(url_for("profile"))
    return render_template("profile.html")

@app.route("/admin")
@admin_required
def admin_dashboard():
    products = Product.query.order_by(Product.id.desc()).all()
    orders = Order.query.order_by(Order.created_at.desc()).limit(12).all()
    total_sales = sum(o.total for o in Order.query.all())
    today = datetime.utcnow().date()
    today_sales = sum(o.total for o in Order.query.all() if o.created_at and o.created_at.date() == today)
    today_orders = sum(1 for o in Order.query.all() if o.created_at and o.created_at.date() == today)
    students_count = User.query.filter_by(role="student").count()
    low_stock = Product.query.filter(Product.stock <= 5).count()
    latest_order = Order.query.order_by(Order.id.desc()).first()
    customer_totals = {}
    for o in Order.query.all():
        customer_totals.setdefault(o.user_id, {"name": o.user.name, "orders": 0, "spent": 0})
        customer_totals[o.user_id]["orders"] += 1
        customer_totals[o.user_id]["spent"] += o.total
    best_customer = max(customer_totals.values(), key=lambda x: (x["spent"], x["orders"]), default=None)
    today_customer = {}
    for o in Order.query.all():
        if o.created_at and o.created_at.date() == today:
            today_customer[o.user_id] = today_customer.get(o.user_id, 0) + o.total
    return render_template("admin_dashboard.html", products=products, orders=orders, total_sales=total_sales,
                           today_sales=today_sales, today_orders=today_orders, students_count=students_count,
                           low_stock=low_stock, latest_order_id=latest_order.id if latest_order else 0,
                           best_customer=best_customer)

@app.route("/admin/products")
@admin_required
def admin_products():
    products = Product.query.order_by(Product.category, Product.name).all()
    categories = [r[0] for r in db.session.query(Product.category).distinct().order_by(Product.category).all()]
    return render_template("admin_products.html", products=products, categories=categories)

@app.route("/admin/orders")
@admin_required
def admin_orders():
    orders = Order.query.order_by(Order.created_at.desc()).all()
    return render_template("admin_orders.html", orders=orders)

@app.route("/admin/users")
@admin_required
def admin_users():
    students = User.query.filter_by(role="student").order_by(User.name).all()
    return render_template("admin_users.html", students=students)


@app.route("/admin/reports")
@admin_required
def admin_reports():
    orders = Order.query.order_by(Order.created_at.asc()).all()
    total_sales = sum(o.total for o in orders)
    total_orders = len(orders)
    product_count = Product.query.count()
    low_stock = Product.query.filter(Product.stock <= 5, Product.stock > 0).count()
    out_stock = Product.query.filter(Product.stock <= 0).count()
    avg_order = total_sales / total_orders if total_orders else 0
    cash_total = sum(o.total for o in orders if o.payment_method == "Cash")
    gcash_total = sum(o.total for o in orders if o.payment_method == "GCash")
    today = datetime.utcnow().date()
    labels, values, sales_values = [], [], []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        labels.append(day.strftime("%a"))
        day_orders = [o for o in orders if o.created_at and o.created_at.date() == day]
        values.append(len(day_orders))
        sales_values.append(round(sum(o.total for o in day_orders), 2))
    top_products = []
    for p in Product.query.all():
        qty = sum(item.quantity for item in p.order_items)
        revenue = sum(item.quantity * item.price for item in p.order_items)
        top_products.append((p.name, qty, revenue))
    top_products.sort(key=lambda x: (x[1], x[2]), reverse=True)
    customer_map = {}
    for o in orders:
        row = customer_map.setdefault(o.user_id, {"name": o.user.name, "student_id": o.user.student_id or "—", "orders": 0, "spent": 0})
        row["orders"] += 1
        row["spent"] += o.total
    top_customers = sorted(customer_map.values(), key=lambda x: (x["spent"], x["orders"]), reverse=True)[:10]
    categories = {}
    for p in Product.query.all():
        categories[p.category] = categories.get(p.category, 0) + sum(i.quantity for i in p.order_items)
    top_categories = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:6]
    peak_day = max(zip(labels, values), key=lambda x: x[1], default=("—", 0))
    repeat_customers = sum(1 for c in customer_map.values() if c["orders"] > 1)
    return render_template("admin_reports.html", total_sales=total_sales, total_orders=total_orders,
                           product_count=product_count, low_stock=low_stock, out_stock=out_stock, avg_order=avg_order,
                           cash_total=cash_total, gcash_total=gcash_total, report_labels=labels, report_values=values,
                           report_sales_values=sales_values, top_products=top_products[:5], top_customers=top_customers,
                           top_categories=top_categories, peak_day=peak_day, repeat_customers=repeat_customers)

@app.get("/admin/reports/export")
@admin_required
def export_reports():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Order Number", "Student", "Total", "Payment Method", "Payment Reference", "Date"])
    for o in Order.query.order_by(Order.created_at.desc()).all():
        writer.writerow([o.order_number, o.user.name, f"{o.total:.2f}", o.payment_method, o.payment_reference or "", o.created_at.strftime("%Y-%m-%d %H:%M")])
    return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=granby_sales_report.csv"})

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        new_password = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if new_password:
            if len(new_password) < 6 or new_password != confirm:
                flash("Password must be at least 6 characters and match the confirmation.", "danger")
                return redirect(url_for("admin_settings"))
            user = current_user()
            user.password_hash = generate_password_hash(new_password)
            log_activity("Changed admin account password", user.id)
            db.session.commit()
            flash("Admin password changed successfully.", "success")
            return redirect(url_for("admin_settings"))
    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(15).all()
    return render_template("admin_settings.html", logs=logs)

@app.post("/admin/reset-system")
@admin_required
def admin_reset_system():
    confirmation = request.form.get("reset_confirmation", "").strip().upper()
    if confirmation != "RESET":
        flash("Reset cancelled. Type RESET exactly to confirm.", "warning")
        return redirect(url_for("admin_settings"))

    try:
        # Remove all application data and recreate the clean default dataset.
        db.session.remove()
        db.drop_all()
        db.create_all()
        seed_database()
        db.session.commit()

        # Remove generated QR codes and uploaded product images from the old dataset.
        qr_folder = os.path.join(BASE_DIR, "qr_codes")
        if os.path.isdir(qr_folder):
            for filename in os.listdir(qr_folder):
                path = os.path.join(qr_folder, filename)
                if os.path.isfile(path):
                    os.remove(path)

        upload_folder = app.config["UPLOAD_FOLDER"]
        if os.path.isdir(upload_folder):
            for filename in os.listdir(upload_folder):
                path = os.path.join(upload_folder, filename)
                if os.path.isfile(path) and filename != ".gitkeep":
                    os.remove(path)

        session.clear()
        flash("System reset successfully. Default admin and sample products were restored.", "success")
        return redirect(url_for("login"))
    except Exception as exc:
        db.session.rollback()
        flash(f"System reset failed: {exc}", "danger")
        return redirect(url_for("admin_settings"))

@app.get("/admin/backup")
@admin_required
def admin_backup():
    return send_file(os.path.join(BASE_DIR, "smart_canteen.db"), as_attachment=True, download_name="granby_smart_canteen_backup.db")

@app.post("/admin/restore")
@admin_required
def admin_restore():
    backup = request.files.get("backup_file")
    if not backup or not backup.filename.lower().endswith(".db"):
        flash("Please choose a valid .db backup file.", "danger")
        return redirect(url_for("admin_settings"))
    temp = os.path.join(BASE_DIR, "restore_temp.db")
    backup.save(temp)
    try:
        db.session.remove()
        db.engine.dispose()
        shutil.copy2(temp, os.path.join(BASE_DIR, "smart_canteen.db"))
        flash("Database restored. Restart the app to ensure all connections use the restored database.", "success")
    except Exception as exc:
        flash(f"Restore failed: {exc}", "danger")
    finally:
        if os.path.exists(temp):
            os.remove(temp)
    return redirect(url_for("admin_settings"))

@app.route("/menu")
def menu():
    category = request.args.get("category", "").strip()
    search = request.args.get("search", "").strip()
    sort = request.args.get("sort", "name").strip()

    query = Product.query.filter_by(available=True)

    if category:
        query = query.filter_by(category=category)
    if search:
        query = query.filter(Product.name.ilike(f"%{search}%"))

    if sort == "price_low":
        query = query.order_by(Product.price.asc())
    elif sort == "price_high":
        query = query.order_by(Product.price.desc())
    else:
        query = query.order_by(Product.category, Product.name)

    products = query.all()
    categories = [row[0] for row in db.session.query(Product.category).distinct().all()]

    return render_template("menu.html", products=products,
                           categories=categories, selected_category=category,
                           search=search, sort=sort)

@app.route("/favorites")
def favorites():
    products = Product.query.filter_by(available=True).order_by(Product.name).all()
    return render_template("favorites.html", products=products)

@app.route("/product/<int:product_id>")
def product_detail(product_id):
    product = db.get_or_404(Product, product_id)
    return render_template("product_detail.html", product=product)

@app.route("/cart")
def cart():
    cart_data = session.get("cart", {})
    items = []
    total = 0

    for product_id, qty in cart_data.items():
        product = db.session.get(Product, int(product_id))
        if product:
            qty = int(qty)
            subtotal = product.price * qty
            items.append({"product": product, "quantity": qty, "subtotal": subtotal})
            total += subtotal

    return render_template("cart.html", items=items, total=total)

@app.post("/cart/add/<int:product_id>")
def add_to_cart(product_id):
    product = db.get_or_404(Product, product_id)

    if not product.available or product.stock <= 0:
        flash("This product is currently unavailable.", "danger")
        return redirect(request.referrer or url_for("menu"))

    qty = max(1, int(request.form.get("quantity", 1)))
    cart_data = session.get("cart", {})
    key = str(product_id)
    new_qty = int(cart_data.get(key, 0)) + qty

    if new_qty > product.stock:
        flash(f"Only {product.stock} item(s) available.", "warning")
        new_qty = product.stock

    cart_data[key] = new_qty
    session["cart"] = cart_data
    flash(f"{product.name} added to cart.", "success")
    return redirect(request.referrer or url_for("menu"))

@app.post("/cart/update")
def update_cart():
    cart_data = session.get("cart", {})

    for key in list(cart_data.keys()):
        raw_qty = request.form.get(f"quantity_{key}", "0")
        try:
            qty = int(raw_qty)
        except ValueError:
            qty = 0

        product = db.session.get(Product, int(key))
        if not product or qty <= 0:
            cart_data.pop(key, None)
        else:
            cart_data[key] = min(qty, product.stock)

    session["cart"] = cart_data
    flash("Cart updated.", "success")
    return redirect(url_for("cart"))

@app.post("/cart/remove/<int:product_id>")
def remove_from_cart(product_id):
    cart_data = session.get("cart", {})
    cart_data.pop(str(product_id), None)
    session["cart"] = cart_data
    flash("Item removed from cart.", "info")
    return redirect(url_for("cart"))

@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    cart_data = session.get("cart", {})
    if not cart_data:
        flash("Your cart is empty.", "warning")
        return redirect(url_for("menu"))

    items = []
    total = 0

    for product_id, qty in cart_data.items():
        product = db.session.get(Product, int(product_id))
        qty = int(qty)

        if not product or not product.available:
            flash("One of the products is no longer available.", "danger")
            return redirect(url_for("cart"))

        if qty > product.stock:
            flash(f"Not enough stock for {product.name}.", "danger")
            return redirect(url_for("cart"))

        subtotal = product.price * qty
        total += subtotal
        items.append((product, qty, subtotal))

    if request.method == "POST":
        payment_method = request.form.get("payment_method", "Cash").strip()
        if payment_method not in {"Cash", "GCash"}:
            payment_method = "Cash"
        payment_reference = request.form.get("payment_reference", "").strip()
        if payment_method == "GCash" and not payment_reference:
            flash("Please enter your GCash reference number.", "danger")
            return render_template("checkout.html", items=items, total=total)

        order_number = "SC-" + uuid.uuid4().hex[:8].upper()
        order = Order(
            order_number=order_number,
            user_id=current_user().id,
            total=total,
            payment_method=payment_method,
            payment_reference=payment_reference,
            pickup_time="",
            status="Pending"
        )
        db.session.add(order)
        db.session.flush()

        for product, qty, subtotal in items:
            product.stock -= qty
            if product.stock == 0:
                product.available = False

            db.session.add(OrderItem(
                order_id=order.id,
                product_id=product.id,
                quantity=qty,
                price=product.price
            ))

        create_notification(
            current_user().id,
            "Order Placed",
            f"Order {order.order_number} has been placed and is waiting for confirmation."
        )

        log_activity(f"Student placed order {order.order_number}", current_user().id)
        db.session.commit()
        session["cart"] = {}

        qr_filename = generate_order_qr(order.order_number)
        flash("Order placed successfully!", "success")
        return redirect(url_for("order_confirmation",
                                order_id=order.id, qr=qr_filename))

    return render_template("checkout.html", items=items, total=total)

@app.route("/qr_codes/<path:filename>")
def order_qr(filename):
    return send_from_directory(os.path.join(BASE_DIR, "qr_codes"), filename)

@app.route("/order/<int:order_id>/confirmation")
@login_required
def order_confirmation(order_id):
    order = db.get_or_404(Order, order_id)

    if order.user_id != current_user().id and current_user().role != "admin":
        abort(403)

    qr_filename = request.args.get("qr") or f"{order.order_number}.png"
    return render_template("order_confirmation.html",
                           order=order, qr_filename=qr_filename)

@app.route("/order/<int:order_id>/receipt")
@login_required
def order_receipt(order_id):
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user().id and current_user().role != "admin":
        abort(403)
    return render_template("receipt.html", order=order)

@app.post("/order/<int:order_id>/reorder")
@login_required
def reorder(order_id):
    order = db.get_or_404(Order, order_id)
    if order.user_id != current_user().id:
        abort(403)
    cart_data = session.get("cart", {})
    added = 0
    for item in order.items:
        product = item.product
        if product and product.available and product.stock > 0:
            key = str(product.id)
            current = int(cart_data.get(key, 0))
            cart_data[key] = min(current + item.quantity, product.stock)
            added += 1
    session["cart"] = cart_data
    flash(f"{added} item(s) from {order.order_number} added to your cart.", "success" if added else "warning")
    return redirect(url_for("cart"))

@app.route("/notifications")
@login_required
def notifications():
    notifications = Notification.query.filter_by(
        user_id=current_user().id
    ).order_by(Notification.created_at.desc()).all()

    for notification in notifications:
        notification.is_read = True
    db.session.commit()

    return render_template("notifications.html", notifications=notifications)

@app.post("/admin/product/add")
@admin_required
def admin_add_product():
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    category = request.form.get("category", "Other").strip()
    image = request.form.get("image", "").strip()
    uploaded = request.files.get("image_file")
    uploaded_url = save_product_image(uploaded)
    if uploaded_url:
        image = uploaded_url

    try:
        price = float(request.form.get("price", 0))
        stock = int(request.form.get("stock", 0))
    except ValueError:
        flash("Price and stock must be valid numbers.", "danger")
        return redirect(url_for("admin_dashboard"))

    if not name or price < 0 or stock < 0:
        flash("Please enter valid product information.", "danger")
        return redirect(url_for("admin_dashboard"))

    db.session.add(Product(
        name=name,
        description=description,
        category=category or "Other",
        price=price,
        stock=stock,
        image=image,
        available=stock > 0
    ))
    log_activity(f"Added product: {name}")
    db.session.commit()

    flash("Product added.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/product/<int:product_id>/update")
@admin_required
def admin_update_product(product_id):
    product = db.get_or_404(Product, product_id)

    product.name = request.form.get("name", product.name).strip()
    product.description = request.form.get("description", product.description).strip()
    product.category = request.form.get("category", product.category).strip()
    uploaded = request.files.get("image_file")
    uploaded_url = save_product_image(uploaded)
    if uploaded_url:
        product.image = uploaded_url

    try:
        product.price = float(request.form.get("price", product.price))
        product.stock = int(request.form.get("stock", product.stock))
    except ValueError:
        flash("Invalid price or stock.", "danger")
        return redirect(url_for("admin_dashboard"))

    product.available = product.stock > 0
    log_activity(f"Updated product: {product.name}")
    db.session.commit()

    flash("Product updated.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/product/<int:product_id>/delete")
@admin_required
def admin_delete_product(product_id):
    product = db.get_or_404(Product, product_id)

    if OrderItem.query.filter_by(product_id=product.id).first():
        product.available = False
        product.stock = 0
        db.session.commit()
        flash("Product has existing orders, so it was marked unavailable instead of deleted.", "info")
    else:
        log_activity(f"Deleted product: {product.name}")
        db.session.delete(product)
        db.session.commit()
        flash("Product deleted.", "success")

    return redirect(url_for("admin_dashboard"))

@app.post("/admin/user/<int:user_id>/delete")
@admin_required
def admin_delete_user(user_id):
    user = db.get_or_404(User, user_id)

    if user.role == "admin":
        flash("Admin accounts cannot be deleted here.", "danger")
        return redirect(url_for("admin_dashboard"))

    if Order.query.filter_by(user_id=user.id).first():
        flash(
            "This student has existing orders, so the account cannot be deleted.",
            "warning"
        )
        return redirect(url_for("admin_dashboard"))

    log_activity(f"Deleted student account: {user.name}")
    db.session.delete(user)
    db.session.commit()

    flash("Student account deleted.", "success")
    return redirect(url_for("admin_dashboard"))

@app.post("/admin/user/<int:user_id>/toggle")
@admin_required
def admin_toggle_user(user_id):
    user = db.get_or_404(User, user_id)
    if user.role == "admin":
        flash("Admin accounts cannot be disabled here.", "danger")
        return redirect(url_for("admin_users"))
    user.is_active = not user.is_active
    action = "Enabled" if user.is_active else "Disabled"
    state = "enabled" if user.is_active else "disabled"
    log_activity(f"{action} student account: {user.name}")
    db.session.commit()
    flash(f"Student account {state}.", "success")
    return redirect(url_for("admin_users"))

@app.get("/service-worker.js")
def service_worker():
    return send_from_directory(os.path.join(app.static_folder, "pwa"), "service-worker.js", mimetype="application/javascript")

@app.get("/admin/latest-order")
@admin_required
def admin_latest_order():
    try:
        since_id = int(request.args.get("since_id", 0))
    except (TypeError, ValueError):
        since_id = 0

    latest = Order.query.order_by(Order.id.desc()).first()
    # If the database was reset, order IDs may start over. Reset the browser cursor.
    if latest and since_id > latest.id:
        since_id = 0
    order = Order.query.filter(Order.id > since_id).order_by(Order.id.asc()).first()

    if not order:
        return {
            "has_order": False,
            "latest_id": latest.id if latest else 0
        }

    return {
        "has_order": True,
        "id": order.id,
        "order_number": order.order_number,
        "student_name": order.user.name,
        "total": float(order.total),
        "payment_method": order.payment_method,
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S")
    }

@app.errorhandler(403)
def forbidden(error):
    return render_template("index.html", products=[], error_message="Access denied."), 403

with app.app_context():
    db.create_all()
    migrate_database()
    seed_database()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
