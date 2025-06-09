from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///inventory.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'my_secret_key'

# Папка для загрузки изображений
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = SQLAlchemy(app)

# Таблица категорий
class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)

    def __repr__(self):
        return f'<Category {self.name}>'

# Таблица товаров
class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.String(200), nullable=True)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    price = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Категория
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=True)
    category = db.relationship('Category', backref=db.backref('products', lazy=True))

    # Фото
    image_filename = db.Column(db.String(200), nullable=True)

    def __repr__(self):
        return f'<Product {self.name}>'

# Создание таблиц
@app.before_first_request
def create_tables():
    db.create_all()

# Главная страница
@app.route('/')
def index():
    search_query = request.args.get('q', '')
    currency = request.args.get('currency', '€')
    category_id = request.args.get('category_id', type=int)

    # Параметры сортировки
    sort = request.args.get('sort', 'id')
    order = request.args.get('order', 'asc')

    # Базовый запрос
    query = Product.query

    # Фильтр по поиску
    if search_query:
        query = query.filter(Product.name.ilike(f'%{search_query}%'))

    # Фильтр по категории
    if category_id:
        query = query.filter(Product.category_id == category_id)

    # Выбор колонки для сортировки
    if sort == 'price':
        sort_column = Product.price
    elif sort == 'quantity':
        sort_column = Product.quantity
    elif sort == 'created_at':
        sort_column = Product.created_at
    else:
        sort_column = Product.id

    # Порядок сортировки
    if order == 'desc':
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())

    products = query.all()
    categories = Category.query.order_by(Category.name).all()

    # Dashboard
    total_products = len(products)
    total_categories = len(categories)
    total_value = sum(p.price * p.quantity for p in products)

    # Последние добавленные товары
    recent_products = Product.query.order_by(Product.created_at.desc()).limit(5).all()

    return render_template('index.html',
                           products=products,
                           categories=categories,
                           currency=currency,
                           search_query=search_query,
                           category_id=category_id,
                           sort=sort,
                           order=order,
                           total_products=total_products,
                           total_categories=total_categories,
                           total_value=total_value,
                           recent_products=recent_products)

# Добавление товара
@app.route('/create', methods=['GET', 'POST'])
def create():
    categories = Category.query.order_by(Category.name).all()

    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        quantity = request.form['quantity']
        price = request.form['price']
        category_id = request.form.get('category_id')

        # Работа с изображением
        image_file = request.files.get('image')
        image_filename = None
        if image_file and image_file.filename:
            filename = secure_filename(image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            image_filename = filename

        if not name or not quantity or not price:
            flash('Заполните все обязательные поля!', 'warning')
            return redirect(url_for('create'))

        try:
            quantity = int(quantity)
            price = float(price)
        except ValueError:
            flash('Количество должно быть целым числом, цена — числом.', 'warning')
            return redirect(url_for('create'))

        new_product = Product(
            name=name,
            description=description,
            quantity=quantity,
            price=price,
            category_id=category_id if category_id else None,
            image_filename=image_filename
        )
        db.session.add(new_product)
        db.session.commit()
        flash(f'Товар "{name}" добавлен!', 'success')
        return redirect(url_for('index'))

    return render_template('create.html', categories=categories)

# Просмотр товара
@app.route('/view/<int:product_id>')
def view(product_id):
    product = Product.query.get_or_404(product_id)
    return render_template('view.html', product=product)

# Добавление категории
@app.route('/add_category', methods=['GET', 'POST'])
def add_category():
    if request.method == 'POST':
        name = request.form['name']
        if not name:
            flash('Введите название категории.', 'warning')
            return redirect(url_for('add_category'))

        if Category.query.filter_by(name=name).first():
            flash('Категория с таким названием уже существует.', 'warning')
            return redirect(url_for('add_category'))

        new_category = Category(name=name)
        db.session.add(new_category)
        db.session.commit()
        flash(f'Категория "{name}" добавлена!', 'success')
        return redirect(url_for('index'))

    return render_template('add_category.html')

# Удаление товара
@app.route('/delete/<int:product_id>', methods=['POST'])
def delete(product_id):
    product = Product.query.get_or_404(product_id)
    db.session.delete(product)
    db.session.commit()
    flash(f'Товар "{product.name}" удалён!', 'success')
    return redirect(url_for('index'))
# Редактирование товара
@app.route('/edit/<int:product_id>', methods=['GET', 'POST'])
def edit(product_id):
    product = Product.query.get_or_404(product_id)
    categories = Category.query.order_by(Category.name).all()

    if request.method == 'POST':
        product.name = request.form['name']
        product.description = request.form['description']
        product.quantity = int(request.form['quantity'])
        product.price = float(request.form['price'])
        category_id = request.form.get('category_id')

        product.category_id = category_id if category_id else None

        # Обновление изображения (если загружено новое)
        image_file = request.files.get('image')
        if image_file and image_file.filename:
            filename = secure_filename(image_file.filename)
            image_file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            product.image_filename = filename

        db.session.commit()
        flash(f'Товар "{product.name}" обновлён!', 'success')
        return redirect(url_for('index'))

    return render_template('edit.html', product=product, categories=categories)

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5111)
