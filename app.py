from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash
import os
import json
import logging
import html
from datetime import datetime, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import gspread
from google.oauth2.service_account import Credentials
from gspread.utils import rowcol_to_a1
import uuid
from functools import wraps
import secrets

# Production setup
app = Flask(__name__)

# Configuration for production
if os.environ.get('RENDER'):
    app.secret_key = os.environ.get('SECRET_KEY')
    if not app.secret_key:
        raise ValueError("SECRET_KEY environment variable not set")

    GOOGLE_SHEETS_CREDENTIALS_JSON = os.environ.get('GOOGLE_SHEETS_CREDENTIALS')
    SPREADSHEET_NAME = os.environ.get('SPREADSHEET_NAME')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD')
    if not ADMIN_PASSWORD:
        raise ValueError("ADMIN_PASSWORD environment variable not set")
    
    # Email configuration
    SMTP_SERVER = os.environ.get('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.environ.get('SMTP_PORT', 587))
    EMAIL_ADDRESS = os.environ.get('EMAIL_ADDRESS')
    EMAIL_PASSWORD = os.environ.get('EMAIL_PASSWORD')
    
    if GOOGLE_SHEETS_CREDENTIALS_JSON:
        credentials_path = '/tmp/credentials.json'
        with open(credentials_path, 'w') as f:
            f.write(GOOGLE_SHEETS_CREDENTIALS_JSON)
        GOOGLE_SHEETS_CREDENTIALS = credentials_path
    else:
        raise ValueError("GOOGLE_SHEETS_CREDENTIALS environment variable not set")
else:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    
    app.secret_key = os.getenv('SECRET_KEY', secrets.token_hex(32))
    GOOGLE_SHEETS_CREDENTIALS = os.getenv("GOOGLE_SHEETS_CREDENTIALS", "credentials.json")
    SPREADSHEET_NAME = os.getenv('SPREADSHEET_NAME', 'POS System Database')
    ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
    
    # Email configuration
    SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    EMAIL_ADDRESS = os.getenv('EMAIL_ADDRESS')
    EMAIL_PASSWORD = os.getenv('EMAIL_PASSWORD')

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not ADMIN_PASSWORD:
    logger.warning("ADMIN_PASSWORD not set. Admin login will be disabled until configured.")

MAX_CART_ITEM_QUANTITY = 100
ADMIN_LOGIN_MAX_ATTEMPTS = 5
ADMIN_LOGIN_LOCKOUT_SECONDS = 300
DEFAULT_PRODUCT_CACHE_TTL_SECONDS = 10

app.config.update(
    JSON_SORT_KEYS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=bool(os.environ.get('RENDER') or os.getenv('SESSION_COOKIE_SECURE') == '1'),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

def require_admin(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            return jsonify({'success': False, 'message': 'Admin access required'}), 401
        return f(*args, **kwargs)
    return decorated_function


@app.before_request
def set_session_defaults():
    session.permanent = True


@app.after_request
def apply_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


def safe_float(value, default=0.0):
    try:
        if value == '' or value is None:
            return default
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return default
            normalized = normalized.replace(',', '').replace('$', '')
            return float(normalized)
        return float(value)
    except (ValueError, TypeError):
        return default

def safe_int(value, default=0):
    try:
        if value == '' or value is None:
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


PRODUCT_CACHE_TTL_SECONDS = max(1, safe_int(os.getenv('PRODUCT_CACHE_TTL_SECONDS'), DEFAULT_PRODUCT_CACHE_TTL_SECONDS))


def get_json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def api_error(message, status_code=400, **extra):
    payload = {'success': False, 'message': message}
    payload.update(extra)
    return jsonify(payload), status_code


def parse_order_datetime(value):
    if value in (None, ''):
        return None

    normalized = str(value).strip()
    if not normalized:
        return None
    if normalized.endswith('Z'):
        normalized = normalized.replace('Z', '+00:00')

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    known_formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y",
    )
    for fmt in known_formats:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def get_session_cart():
    cart_items = session.get('cart', [])
    if not isinstance(cart_items, list):
        cart_items = []

    sanitized_items = []
    for item in cart_items:
        if not isinstance(item, dict):
            continue

        quantity = safe_int(item.get('quantity'), 0)
        unit_price = safe_float(item.get('unit_price'), 0.0)
        product_id = str(item.get('product_id', '')).strip()

        if quantity <= 0 or not product_id:
            continue

        sanitized_items.append({
            'product_id': product_id,
            'name': str(item.get('name', '')).strip(),
            'unit_price': unit_price,
            'quantity': quantity,
            'total_price': round(unit_price * quantity, 2)
        })

    session['cart'] = sanitized_items
    session.modified = True
    return sanitized_items


def save_session_cart(cart_items):
    session['cart'] = cart_items
    session.modified = True


def is_admin_login_locked():
    lock_until = session.get('admin_lock_until')
    if not lock_until:
        return False, 0

    try:
        lock_until_dt = datetime.fromisoformat(lock_until)
    except ValueError:
        session.pop('admin_lock_until', None)
        session.pop('admin_login_attempts', None)
        return False, 0

    now = datetime.utcnow()
    if now >= lock_until_dt:
        session.pop('admin_lock_until', None)
        session.pop('admin_login_attempts', None)
        return False, 0

    remaining = int((lock_until_dt - now).total_seconds())
    return True, max(1, remaining)

def validate_product_data(data):
    required_fields = ['name', 'price', 'stock', 'category']
    for field in required_fields:
        if not data.get(field):
            return False, f"Missing required field: {field}"
    
    try:
        price = float(data['price'])
        stock = int(data['stock'])
        import_price = float(data.get('import_price', 0))
        if price < 0 or stock < 0 or import_price < 0:
            return False, "Price, import price, and stock must be non-negative"
    except (ValueError, TypeError):
        return False, "Invalid price, import price, or stock value"
    
    return True, ""


def generate_invoice_html(order_data, company_info):
    """Generate HTML invoice content with proper discount and delivery calculations"""

    order_data = order_data if isinstance(order_data, dict) else {}
    company_info = company_info if isinstance(company_info, dict) else {}

    def esc(value):
        return html.escape(str(value if value is not None else ""))

    subtotal = safe_float(order_data.get('subtotal'), 0.0)
    discount_amount = safe_float(order_data.get('discount_amount'), 0.0)
    delivery_fee = safe_float(order_data.get('delivery_fee'), 0.0)
    final_total = safe_float(order_data.get('total'), subtotal - discount_amount + delivery_fee)
    payment_method = esc(order_data.get('payment_method', 'Cash'))
    amount_received = safe_float(order_data.get('amount_received'), 0.0)
    change = safe_float(order_data.get('change'), 0.0)

    raw_date = str(order_data.get('date', '')).replace('Z', '+00:00')
    try:
        invoice_date = datetime.fromisoformat(raw_date) if raw_date else datetime.now()
    except ValueError:
        invoice_date = datetime.now()

    safe_items = []
    for item in order_data.get('items', []):
        if not isinstance(item, dict):
            continue
        quantity = safe_int(item.get('quantity'), 0)
        unit_price = safe_float(item.get('unit_price'), 0.0)
        total_price = safe_float(item.get('total_price'), unit_price * quantity)
        safe_items.append({
            'name': esc(item.get('name', '')),
            'quantity': quantity,
            'unit_price': unit_price,
            'total_price': total_price
        })

    items_html = ''.join(
        f"""
                <tr>
                    <td>{item['name']}</td>
                    <td class=\"text-right\">{item['quantity']}</td>
                    <td class=\"text-right\">${item['unit_price']:.2f}</td>
                    <td class=\"text-right\">${item['total_price']:.2f}</td>
                </tr>
                """
        for item in safe_items
    )

    company_name = esc(company_info.get('name', 'POS System Store'))
    company_address = esc(company_info.get('address', ''))
    company_city = esc(company_info.get('city', ''))
    company_phone = esc(company_info.get('phone', ''))
    company_email = esc(company_info.get('email', ''))
    company_website = esc(company_info.get('website', ''))
    invoice_number = esc(order_data.get('invoice_number', 'N/A'))
    order_id = esc(order_data.get('order_id', 'N/A'))
    customer_name = esc(order_data.get('customer_name', 'Walk-in Customer'))
    customer_phone = esc(order_data.get('customer_phone', ''))
    customer_address = esc(order_data.get('customer_address', ''))

    invoice_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Invoice - {invoice_number}</title>
        <style>
            body {{ 
                font-family: Arial, sans-serif; 
                margin: 0; 
                padding: 20px; 
                font-size: 12px;
                color: #333;
            }}
            .invoice-header {{ 
                display: flex; 
                justify-content: space-between; 
                margin-bottom: 30px; 
                border-bottom: 2px solid #333;
                padding-bottom: 15px;
            }}
            .company-info h1 {{ 
                margin: 0 0 10px 0; 
                font-size: 24px;
                color: #2c3e50;
            }}
            .company-info p {{ margin: 2px 0; }}
            .invoice-details {{ text-align: right; }}
            .invoice-details h2 {{ 
                margin: 0 0 10px 0; 
                font-size: 28px;
                color: #e74c3c;
            }}
            .customer-info {{ 
                margin-bottom: 30px; 
                padding: 15px;
                background-color: #f8f9fa;
                border-left: 4px solid #007bff;
            }}
            .customer-info h3 {{ 
                margin: 0 0 10px 0; 
                color: #007bff;
            }}
            table {{ 
                width: 100%; 
                border-collapse: collapse; 
                margin-bottom: 30px;
            }}
            th, td {{ 
                padding: 12px; 
                text-align: left; 
                border-bottom: 1px solid #ddd; 
            }}
            th {{ 
                background-color: #f8f9fa; 
                font-weight: bold;
                color: #495057;
            }}
            .text-right {{ text-align: right; }}
            .invoice-summary {{ 
                float: right; 
                width: 300px; 
                margin-top: 20px;
            }}
            .summary-row {{ 
                display: flex; 
                justify-content: space-between; 
                padding: 8px 0;
                border-bottom: 1px solid #eee;
            }}
            .total-row {{ 
                font-weight: bold; 
                font-size: 16px;
                border-top: 2px solid #333;
                border-bottom: 2px solid #333;
                background-color: #f8f9fa;
                padding: 15px 0;
                margin-top: 10px;
            }}
            .discount-row {{
                color: #28a745;
                font-weight: bold;
            }}
            .delivery-row {{
                color: #17a2b8;
                font-weight: bold;
            }}
            .payment-info {{
                margin-top: 15px;
                padding-top: 15px;
                border-top: 1px solid #ddd;
            }}
            .footer {{ 
                text-align: center; 
                margin-top: 50px; 
                padding-top: 20px;
                border-top: 1px solid #ddd;
                color: #666;
            }}
            .payment-method {{
                clear: both;
                margin-top: 30px;
                padding: 15px;
                background-color: #e9ecef;
                border-radius: 5px;
                border-left: 4px solid #28a745;
            }}
        </style>
    </head>
    <body>
        <div class="invoice-header">
            <div class="company-info">
                <h1>{company_name}</h1>
                <p>{company_address}</p>
                <p>{company_city}</p>
                <p>Phone: {company_phone}</p>
                <p>Email: {company_email}</p>
            </div>
            <div class="invoice-details">
                <h2>INVOICE</h2>
                <p><strong>Invoice #:</strong> {invoice_number}</p>
                <p><strong>Order ID:</strong> {order_id}</p>
                <p><strong>Date:</strong> {invoice_date.strftime('%Y-%m-%d')}</p>
                <p><strong>Time:</strong> {invoice_date.strftime('%H:%M:%S')}</p>
            </div>
        </div>

        <div class="customer-info">
            <h3>Bill To:</h3>
            <p><strong>{customer_name}</strong></p>
            {f"<p>Phone: {customer_phone}</p>" if customer_phone else ''}
            {f"<p>Address: {customer_address}</p>" if customer_address else ''}
        </div>

        <table>
            <thead>
                <tr>
                    <th>Item Description</th>
                    <th class="text-right">Qty</th>
                    <th class="text-right">Unit Price</th>
                    <th class="text-right">Total</th>
                </tr>
            </thead>
            <tbody>
                {items_html}
            </tbody>
        </table>

        <div class="invoice-summary">
            <div class="summary-row">
                <span>Subtotal:</span>
                <span>${subtotal:.2f}</span>
            </div>
            {f'''
            <div class="summary-row discount-row">
                <span>Discount:</span>
                <span>-${discount_amount:.2f}</span>
            </div>
            ''' if discount_amount > 0 else ''}
            {f'''
            <div class="summary-row delivery-row">
                <span>Delivery Fee:</span>
                <span>${delivery_fee:.2f}</span>
            </div>
            ''' if delivery_fee > 0 else ''}
            <div class="summary-row">
                <span>Tax (0%):</span>
                <span>$0.00</span>
            </div>
            <div class="summary-row total-row">
                <span>FINAL TOTAL:</span>
                <span>${final_total:.2f}</span>
            </div>
            {f'''
            <div class="payment-info">
                <div class="summary-row">
                    <span>Amount Received:</span>
                    <span>${amount_received:.2f}</span>
                </div>
                <div class="summary-row">
                    <span>Change:</span>
                    <span>${change:.2f}</span>
                </div>
            </div>
            ''' if payment_method == 'Cash' else ''}
        </div>

        <div class="payment-method">
            <p><strong>Payment Method:</strong> {payment_method}</p>
        </div>

        <div class="footer">
            <p><strong>Thank you for your business!</strong></p>
            <p>Questions? Contact us at {company_phone} or {company_email}</p>
            <p>Website: {company_website}</p>
        </div>
    </body>
    </html>
    """
    
    return invoice_html


class GoogleSheetsManager:
    def __init__(self, credentials_file: str, spreadsheet_name: str):
        self.credentials_file = credentials_file
        self.spreadsheet_name = spreadsheet_name
        self.client = None
        self.spreadsheet = None
        self._products_cache = []
        self._products_cache_at = None
        self.connect()
    
    def connect(self):
        try:
            scope = [
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive'
            ]
            creds = Credentials.from_service_account_file(self.credentials_file, scopes=scope)
            self.client = gspread.authorize(creds)
            
            try:
                self.spreadsheet = self.client.open(self.spreadsheet_name)
                logger.info(f"Connected to existing spreadsheet: {self.spreadsheet_name}")
            except gspread.SpreadsheetNotFound:
                self.spreadsheet = self.client.create(self.spreadsheet_name)
                logger.info(f"Created new spreadsheet: {self.spreadsheet_name}")
            
            self.setup_worksheets()
            self.invalidate_products_cache()
        except Exception as e:
            logger.error(f"Error connecting to Google Sheets: {e}")

    def invalidate_products_cache(self):
        self._products_cache = []
        self._products_cache_at = None

    def _products_cache_valid(self):
        if self._products_cache_at is None:
            return False
        age_seconds = (datetime.utcnow() - self._products_cache_at).total_seconds()
        return age_seconds <= PRODUCT_CACHE_TTL_SECONDS

    @staticmethod
    def _find_column_index(headers, candidates):
        normalized_candidates = {str(candidate).strip().lower() for candidate in candidates}
        for idx, header in enumerate(headers, start=1):
            if str(header).strip().lower() in normalized_candidates:
                return idx
        return None

    def _get_products_columns(self, worksheet):
        headers = [str(h).strip() for h in worksheet.row_values(1)]
        if not headers:
            return {}, []

        columns = {
            'id': self._find_column_index(headers, ('ID',)),
            'name': self._find_column_index(headers, ('Name',)),
            'price': self._find_column_index(headers, ('Price',)),
            'stock': self._find_column_index(headers, ('Stock',)),
            'category': self._find_column_index(headers, ('Category',)),
            'description': self._find_column_index(headers, ('Description',)),
            'created_at': self._find_column_index(headers, ('Created_At',)),
            'updated_at': self._find_column_index(headers, ('Updated_At',)),
            'import_price': self._find_column_index(headers, ('Import_Price', 'Import Price')),
        }
        return columns, headers

    @staticmethod
    def _batch_update_cells(worksheet, updates):
        if not updates:
            return
        payload = [
            {'range': rowcol_to_a1(row, col), 'values': [[value]]}
            for row, col, value in updates
        ]
        worksheet.batch_update(payload, value_input_option='RAW')

    @staticmethod
    def _build_row_lookup(values):
        lookup = {}
        for row_idx, raw in enumerate(values[1:], start=2):
            key = str(raw).strip()
            if key:
                lookup[key] = row_idx
        return lookup
    
    def setup_worksheets(self):
        worksheets_config = {
            'Products': ['ID', 'Name', 'Price', 'Stock', 'Category', 'Description', 'Created_At', 'Updated_At', 'Import_Price'],
            'Orders': ['Order_ID', 'Customer_Name', 'Customer_Phone', 'Customer_Address', 'Items', 'Subtotal', 'Discount_Amount', 'Delivery_Fee', 'Total_Amount', 'Status', 'Order_Date', 'Payment_Method', 'Amount_Received'],
            'Customers': ['Customer_ID', 'Name', 'Phone', 'Email', 'Total_Orders', 'Total_Spent', 'Last_Visit'],
            'Inventory_Log': ['ID', 'Product_ID', 'Action', 'Quantity_Change', 'Previous_Stock', 'New_Stock', 'Date', 'Reason']
        }
        
        for sheet_name, headers in worksheets_config.items():
            try:
                worksheet = self.spreadsheet.worksheet(sheet_name)
                # Check if we need to add new columns for existing sheets
                if sheet_name == 'Products':
                    existing_headers = [str(h).strip() for h in worksheet.row_values(1)]
                    import_price_col = self._find_column_index(existing_headers, ('Import_Price', 'Import Price'))
                    if import_price_col is None:
                        worksheet.update_cell(1, len(existing_headers) + 1, 'Import_Price')
                        logger.info("Added Import_Price column to existing Products worksheet")
                    elif existing_headers[import_price_col - 1] != 'Import_Price':
                        worksheet.update_cell(1, import_price_col, 'Import_Price')
                        logger.info("Normalized product cost column header to Import_Price")
                elif sheet_name == 'Orders':
                    existing_headers = worksheet.row_values(1)
                    # Add missing columns for discount and delivery tracking
                    missing_cols = ['Subtotal', 'Discount_Amount', 'Delivery_Fee']
                    for col in missing_cols:
                        if col not in existing_headers:
                            worksheet.update_cell(1, len(existing_headers) + 1, col)
                            existing_headers.append(col)
                            logger.info(f"Added {col} column to existing Orders worksheet")
            except gspread.WorksheetNotFound:
                worksheet = self.spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=len(headers))
                worksheet.append_row(headers)
                logger.info(f"Created worksheet: {sheet_name}")

    def add_order(self, order_data: dict):
        try:
            orders_worksheet = self.spreadsheet.worksheet('Orders')
            order_row = [
                order_data.get('Order_ID', ''),
                order_data.get('Customer_Name', ''),
                order_data.get('Customer_Phone', ''),
                order_data.get('Customer_Address', ''),
                json.dumps(order_data.get('Items', [])),
                order_data.get('Subtotal', 0),  # Add subtotal
                order_data.get('Discount_Amount', 0),  # Add discount
                order_data.get('Delivery_Fee', 0),  # Add delivery fee
                order_data.get('Total_Amount', 0),
                order_data.get('Status', 'Completed'),
                order_data.get('Order_Date', datetime.now().isoformat()),
                order_data.get('Payment_Method', 'Cash'),
                order_data.get('Amount_Received', 0)
            ]

            orders_worksheet.append_row(order_row)

            # Batch stock deductions to minimize Google Sheets API calls.
            products = self.get_products(force_refresh=True)
            product_map = {p['ID']: p for p in products}
            quantity_by_product = {}
            for item in order_data.get('Items', []):
                product_id = str(item.get('product_id', '')).strip()
                quantity = safe_int(item.get('quantity'), 0)
                if not product_id or quantity <= 0:
                    continue
                quantity_by_product[product_id] = quantity_by_product.get(product_id, 0) + quantity

            if quantity_by_product:
                products_worksheet = self.spreadsheet.worksheet('Products')
                columns, _ = self._get_products_columns(products_worksheet)
                id_col = columns.get('id')
                stock_col = columns.get('stock')
                updated_at_col = columns.get('updated_at')
                if id_col and stock_col:
                    id_values = products_worksheet.col_values(id_col)
                    row_lookup = self._build_row_lookup(id_values)
                    timestamp = datetime.now().isoformat()

                    stock_updates = []
                    inventory_logs = []

                    for product_id, quantity in quantity_by_product.items():
                        product = product_map.get(product_id)
                        row_idx = row_lookup.get(product_id)
                        if not product or row_idx is None:
                            continue

                        previous_stock = max(0, safe_int(product.get('Stock'), 0))
                        new_stock = max(0, previous_stock - quantity)

                        stock_updates.append((row_idx, stock_col, new_stock))
                        if updated_at_col is not None:
                            stock_updates.append((row_idx, updated_at_col, timestamp))

                        inventory_logs.append([
                            str(uuid.uuid4())[:8].upper(),
                            product_id,
                            "UPDATE",
                            new_stock - previous_stock,
                            previous_stock,
                            new_stock,
                            timestamp,
                            f"Order {order_data.get('Order_ID')}"
                        ])

                    self._batch_update_cells(products_worksheet, stock_updates)

                    if inventory_logs:
                        inventory_worksheet = self.spreadsheet.worksheet('Inventory_Log')
                        inventory_worksheet.append_rows(inventory_logs, value_input_option='RAW')

                    self.invalidate_products_cache()
            
            return True
        except Exception as e:
            logger.error(f"Error adding order: {e}")
            return False

    def get_products(self, force_refresh: bool = False):
        try:
            if not force_refresh and self._products_cache_valid():
                return [dict(product) for product in self._products_cache]

            worksheet = self.spreadsheet.worksheet('Products')
            all_data = worksheet.get_all_values()
            if not all_data:
                return []
            
            headers = all_data[0]
            rows = all_data[1:]
            
            records = []
            for row in rows:
                if not any(str(cell).strip() for cell in row):
                    continue
                record = {}
                for i, header in enumerate(headers):
                    header_name = str(header).strip()
                    if not header_name:
                        continue
                    record[header_name] = row[i] if i < len(row) else ''
                records.append(record)
            
            valid_products = []
            for record in records:
                name = record.get('Name', '').strip()
                product_id = record.get('ID', '').strip()
                
                if not name or not product_id:
                    continue
                
                try:
                    record['Price'] = safe_float(record.get('Price', 0))
                    record['Stock'] = safe_int(record.get('Stock', 0))
                    import_price_raw = record.get('Import_Price', '')
                    if import_price_raw in ('', None):
                        import_price_raw = record.get('Import Price', 0)
                    record['Import_Price'] = safe_float(import_price_raw, 0)
                    record['Name'] = name
                    record['ID'] = product_id
                    record['Category'] = record.get('Category', 'General').strip()
                    record['Description'] = record.get('Description', '').strip()
                    valid_products.append(record)
                except Exception as e:
                    continue
            
            self._products_cache = [dict(product) for product in valid_products]
            self._products_cache_at = datetime.utcnow()
            return [dict(product) for product in valid_products]
        except Exception as e:
            logger.error(f"Error getting products: {e}")
            return []
    
    def add_product(self, product_data: dict):
        try:
            worksheet = self.spreadsheet.worksheet('Products')
            product_data['Created_At'] = datetime.now().isoformat()
            product_data['Updated_At'] = datetime.now().isoformat()

            headers = [str(h).strip() for h in worksheet.row_values(1)]
            if not headers:
                headers = ['ID', 'Name', 'Price', 'Stock', 'Category', 'Description', 'Created_At', 'Updated_At', 'Import_Price']
                worksheet.append_row(headers)

            row = []
            for header in headers:
                if header in ('Import_Price', 'Import Price'):
                    row.append(product_data.get('Import_Price', 0))
                elif header == 'ID':
                    row.append(product_data.get('ID', ''))
                elif header == 'Name':
                    row.append(product_data.get('Name', ''))
                elif header == 'Price':
                    row.append(product_data.get('Price', 0))
                elif header == 'Stock':
                    row.append(product_data.get('Stock', 0))
                elif header == 'Category':
                    row.append(product_data.get('Category', ''))
                elif header == 'Description':
                    row.append(product_data.get('Description', ''))
                elif header == 'Created_At':
                    row.append(product_data.get('Created_At', ''))
                elif header == 'Updated_At':
                    row.append(product_data.get('Updated_At', ''))
                else:
                    row.append('')
            worksheet.append_row(row)
            self.invalidate_products_cache()
            return True
        except Exception as e:
            logger.error(f"Error adding product: {e}")
            return False
    
    def update_product_stock(self, product_id: str, new_stock: int, reason: str = "Manual Update"):
        try:
            worksheet = self.spreadsheet.worksheet('Products')
            columns, _ = self._get_products_columns(worksheet)
            id_col = columns.get('id')
            stock_col = columns.get('stock')
            updated_at_col = columns.get('updated_at')
            if id_col is None or stock_col is None:
                logger.error("Products worksheet is missing ID or Stock column")
                return False

            id_values = worksheet.col_values(id_col)
            row_lookup = self._build_row_lookup(id_values)
            row_idx = row_lookup.get(product_id)
            if row_idx is None:
                return False

            row_data = worksheet.row_values(row_idx)
            old_stock = safe_int(row_data[stock_col - 1] if len(row_data) >= stock_col else 0)
            updates = [(row_idx, stock_col, new_stock)]
            timestamp = datetime.now().isoformat()
            if updated_at_col is not None:
                updates.append((row_idx, updated_at_col, timestamp))
            self._batch_update_cells(worksheet, updates)

            self.log_inventory_change(product_id, "UPDATE", new_stock - old_stock, old_stock, new_stock, reason)
            self.invalidate_products_cache()
            return True
        except Exception as e:
            logger.error(f"Error updating product stock: {e}")
            return False
    
    def add_stock_to_product(self, product_id: str, stock_to_add: int, reason: str = "Stock Addition"):
        try:
            worksheet = self.spreadsheet.worksheet('Products')
            columns, _ = self._get_products_columns(worksheet)
            id_col = columns.get('id')
            stock_col = columns.get('stock')
            updated_at_col = columns.get('updated_at')
            if id_col is None or stock_col is None:
                logger.error("Products worksheet is missing ID or Stock column")
                return False

            id_values = worksheet.col_values(id_col)
            row_lookup = self._build_row_lookup(id_values)
            row_idx = row_lookup.get(product_id)
            if row_idx is None:
                return False

            row_data = worksheet.row_values(row_idx)
            old_stock = safe_int(row_data[stock_col - 1] if len(row_data) >= stock_col else 0)
            new_stock = old_stock + stock_to_add

            updates = [(row_idx, stock_col, new_stock)]
            timestamp = datetime.now().isoformat()
            if updated_at_col is not None:
                updates.append((row_idx, updated_at_col, timestamp))
            self._batch_update_cells(worksheet, updates)

            self.log_inventory_change(product_id, "ADD_STOCK", stock_to_add, old_stock, new_stock, reason)
            self.invalidate_products_cache()
            return True
        except Exception as e:
            logger.error(f"Error adding stock to product: {e}")
            return False
    
    def update_product(self, product_id: str, product_data: dict):
        try:
            worksheet = self.spreadsheet.worksheet('Products')
            columns, headers = self._get_products_columns(worksheet)
            id_col = columns.get('id')
            name_col = columns.get('name')
            price_col = columns.get('price')
            category_col = columns.get('category')
            description_col = columns.get('description')
            import_price_col = columns.get('import_price')
            updated_at_col = columns.get('updated_at')

            if id_col is None:
                logger.error("Products worksheet is missing ID column")
                return False

            id_values = worksheet.col_values(id_col)
            row_lookup = self._build_row_lookup(id_values)
            row_idx = row_lookup.get(product_id)
            if row_idx is None:
                return False

            updates = []
            if 'name' in product_data and name_col is not None:
                updates.append((row_idx, name_col, product_data['name']))
            if 'price' in product_data and price_col is not None:
                updates.append((row_idx, price_col, product_data['price']))
            if 'category' in product_data and category_col is not None:
                updates.append((row_idx, category_col, product_data['category']))
            if 'description' in product_data and description_col is not None:
                updates.append((row_idx, description_col, product_data['description']))
            if 'import_price' in product_data:
                if import_price_col is None:
                    import_price_col = len(headers) + 1
                    worksheet.update_cell(1, import_price_col, 'Import_Price')
                updates.append((row_idx, import_price_col, product_data['import_price']))

            if updated_at_col is not None:
                updates.append((row_idx, updated_at_col, datetime.now().isoformat()))

            self._batch_update_cells(worksheet, updates)
            self.invalidate_products_cache()
            return True
        except Exception as e:
            logger.error(f"Error updating product: {e}")
            return False
    
    def log_inventory_change(self, product_id: str, action: str, quantity_change: int, previous_stock: int, new_stock: int, reason: str):
        try:
            worksheet = self.spreadsheet.worksheet('Inventory_Log')
            log_entry = [
                str(uuid.uuid4())[:8].upper(),
                product_id,
                action,
                quantity_change,
                previous_stock,
                new_stock,
                datetime.now().isoformat(),
                reason
            ]
            worksheet.append_row(log_entry)
        except Exception as e:
            logger.error(f"Error logging inventory change: {e}")
    
    def get_orders(self, limit: int = 50):
        try:
            worksheet = self.spreadsheet.worksheet('Orders')
            records = worksheet.get_all_records()
            for record in records:
                record['Subtotal'] = safe_float(record.get('Subtotal', 0))
                record['Discount_Amount'] = safe_float(record.get('Discount_Amount', 0))
                record['Delivery_Fee'] = safe_float(record.get('Delivery_Fee', 0))
                record['Total_Amount'] = safe_float(record.get('Total_Amount', 0))
                record['Amount_Received'] = safe_float(record.get('Amount_Received', 0))

            records.sort(
                key=lambda order: parse_order_datetime(order.get('Order_Date')) or datetime.min,
                reverse=True
            )
            return records[:limit] if len(records) > limit else records
        except Exception as e:
            logger.error(f"Error getting orders: {e}")
            return []
    
    def get_low_stock_products(self, threshold: int = 10):
        products = self.get_products()
        return [p for p in products if p['Stock'] <= threshold]
    
    def get_inventory_log(self, limit: int = 100):
        try:
            worksheet = self.spreadsheet.worksheet('Inventory_Log')
            records = worksheet.get_all_records()
            return records[-limit:] if len(records) > limit else records
        except Exception as e:
            logger.error(f"Error getting inventory log: {e}")
            return []

# Initialize
sheets_manager = GoogleSheetsManager(GOOGLE_SHEETS_CREDENTIALS, SPREADSHEET_NAME)

# Routes
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
        'google_sheets_connected': sheets_manager.spreadsheet is not None,
        'product_cache_ttl_seconds': PRODUCT_CACHE_TTL_SECONDS
    })

@app.route('/admin')
def admin():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    return render_template('admin.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if not ADMIN_PASSWORD:
        flash('Admin login is not configured. Set ADMIN_PASSWORD in environment variables.', 'error')
        return render_template('admin_login.html')

    if request.method == 'POST':
        is_locked, remaining_seconds = is_admin_login_locked()
        if is_locked:
            flash(f'Too many attempts. Try again in {remaining_seconds} seconds.', 'error')
            return render_template('admin_login.html')

        password = request.form.get('password', '')
        if password == ADMIN_PASSWORD:
            session['is_admin'] = True
            session.pop('admin_login_attempts', None)
            session.pop('admin_lock_until', None)
            flash('Login successful!', 'success')
            return redirect(url_for('admin'))

        attempts = safe_int(session.get('admin_login_attempts'), 0) + 1
        if attempts >= ADMIN_LOGIN_MAX_ATTEMPTS:
            lock_until = datetime.utcnow() + timedelta(seconds=ADMIN_LOGIN_LOCKOUT_SECONDS)
            session['admin_lock_until'] = lock_until.isoformat()
            session.pop('admin_login_attempts', None)
            flash(f'Too many failed attempts. Login locked for {ADMIN_LOGIN_LOCKOUT_SECONDS} seconds.', 'error')
        else:
            session['admin_login_attempts'] = attempts
            remaining_attempts = ADMIN_LOGIN_MAX_ATTEMPTS - attempts
            flash(f'Invalid password! {remaining_attempts} attempt(s) left.', 'error')

    return render_template('admin_login.html')

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    session.pop('admin_login_attempts', None)
    session.pop('admin_lock_until', None)
    flash('Logged out successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/api/products')
def get_products():
    products = sheets_manager.get_products()
    return jsonify(products)

@app.route('/api/products/search')
def search_products():
    query = request.args.get('q', '').lower()
    products = sheets_manager.get_products()
    
    if not query:
        return jsonify(products)
    
    filtered = []
    for product in products:
        name = str(product.get('Name', '')).lower()
        category = str(product.get('Category', '')).lower()
        description = str(product.get('Description', '')).lower()
        if query in name or query in category or query in description:
            filtered.append(product)
    
    return jsonify(filtered)

@app.route('/api/categories')
def get_categories():
    products = sheets_manager.get_products()
    categories = {}
    
    for product in products:
        cat = product.get('Category', 'General')
        categories[cat] = categories.get(cat, 0) + 1
    
    return jsonify(categories)

@app.route('/api/products/category/<category>')
def get_products_by_category(category):
    products = sheets_manager.get_products()
    category_lower = category.lower()
    filtered = [p for p in products if str(p.get('Category', 'General')).lower() == category_lower]
    return jsonify(filtered)

@app.route('/api/cart')
def get_cart():
    return jsonify(get_session_cart())

@app.route('/api/cart/add', methods=['POST'])
def add_to_cart():
    data = get_json_body()
    product_id = str(data.get('product_id', '')).strip()
    quantity = safe_int(data.get('quantity'), 1)

    if not product_id:
        return api_error('Product ID is required', 400)

    if quantity <= 0 or quantity > MAX_CART_ITEM_QUANTITY:
        return api_error('Invalid quantity', 400)

    products = sheets_manager.get_products()
    product = next((p for p in products if p['ID'] == product_id), None)

    if not product:
        return api_error('Product not found', 404)

    if product['Stock'] < quantity:
        return api_error('Insufficient stock', 409)

    cart = get_session_cart()
    existing_item = next((item for item in cart if item['product_id'] == product_id), None)

    if existing_item:
        new_quantity = existing_item['quantity'] + quantity
        if product['Stock'] < new_quantity:
            return api_error('Insufficient stock for total quantity', 409)
        existing_item['quantity'] = new_quantity
        existing_item['unit_price'] = safe_float(product.get('Price'), existing_item['unit_price'])
        existing_item['total_price'] = round(existing_item['quantity'] * existing_item['unit_price'], 2)
        existing_item['name'] = product.get('Name', existing_item['name'])
    else:
        cart_item = {
            'product_id': product_id,
            'name': product.get('Name', ''),
            'unit_price': safe_float(product.get('Price'), 0.0),
            'quantity': quantity,
            'total_price': round(safe_float(product.get('Price'), 0.0) * quantity, 2)
        }
        cart.append(cart_item)

    save_session_cart(cart)
    return jsonify({'success': True, 'cart_count': len(cart), 'cart': cart})

@app.route('/api/cart/update', methods=['POST'])
def update_cart():
    data = get_json_body()
    product_id = str(data.get('product_id', '')).strip()
    quantity = safe_int(data.get('quantity'), 0)

    if not product_id:
        return api_error('Product ID is required', 400)

    cart = get_session_cart()
    item = next((entry for entry in cart if entry['product_id'] == product_id), None)
    if not item:
        return api_error('Item not found in cart', 404)

    if quantity <= 0:
        cart = [entry for entry in cart if entry['product_id'] != product_id]
        save_session_cart(cart)
        return jsonify({'success': True, 'cart': cart})

    if quantity > MAX_CART_ITEM_QUANTITY:
        return api_error('Quantity too large', 400)

    products = sheets_manager.get_products()
    product = next((p for p in products if p['ID'] == product_id), None)
    if not product:
        return api_error('Product not found', 404)
    if product['Stock'] < quantity:
        return api_error('Insufficient stock', 409)

    item['quantity'] = quantity
    item['unit_price'] = safe_float(product.get('Price'), item['unit_price'])
    item['name'] = product.get('Name', item['name'])
    item['total_price'] = round(item['quantity'] * item['unit_price'], 2)

    save_session_cart(cart)
    return jsonify({'success': True, 'cart': cart})

@app.route('/api/cart/remove', methods=['POST'])
def remove_from_cart():
    data = get_json_body()
    product_id = str(data.get('product_id', '')).strip()
    cart = [item for item in get_session_cart() if item['product_id'] != product_id]
    save_session_cart(cart)
    return jsonify({'success': True, 'cart': cart})

@app.route('/api/cart/clear', methods=['POST'])
def clear_cart():
    cart = []
    save_session_cart(cart)
    return jsonify({'success': True, 'cart': cart})

@app.route('/api/checkout', methods=['POST'])
def checkout():
    data = get_json_body()
    cart = get_session_cart()

    if not cart:
        return api_error('Cart is empty', 400)

    customer_name = str(data.get('customer_name', 'Walk-in Customer')).strip() or 'Walk-in Customer'
    customer_phone = str(data.get('customer_phone', '')).strip()
    customer_address = str(data.get('customer_address', '')).strip()
    discount_amount = max(0.0, safe_float(data.get('discount_amount'), 0.0))
    delivery_fee = max(0.0, safe_float(data.get('delivery_fee'), 0.0))
    payment_method = str(data.get('payment_method', 'Cash')).strip()
    amount_received = max(0.0, safe_float(data.get('amount_received'), 0.0))
    print_size = str(data.get('print_size', '80mm')).strip()

    valid_payment_methods = {'Cash', 'Card', 'Digital'}
    if payment_method not in valid_payment_methods:
        payment_method = 'Cash'

    valid_print_sizes = {'80mm', '100mm', 'A4', 'A5', 'letter'}
    if print_size not in valid_print_sizes:
        print_size = '80mm'

    products = sheets_manager.get_products(force_refresh=True)
    product_map = {product['ID']: product for product in products}
    for item in cart:
        product = product_map.get(item['product_id'])
        if not product:
            return api_error(f"Product {item['product_id']} no longer exists", 404)
        if safe_int(product.get('Stock'), 0) < item['quantity']:
            return api_error(f"Insufficient stock for {product.get('Name', item['product_id'])}", 409)

    order_id = f"ORD-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
    subtotal = sum(safe_float(item.get('total_price'), 0.0) for item in cart)
    final_total = max(0, subtotal - discount_amount + delivery_fee)

    # Validate payment amount for cash transactions
    if payment_method == 'Cash' and amount_received < final_total:
        return api_error('Insufficient payment amount', 400)

    order_data = {
        'Order_ID': order_id,
        'Customer_Name': customer_name,
        'Customer_Phone': customer_phone,
        'Customer_Address': customer_address,
        'Items': cart.copy(),  # Make a copy to preserve original cart data
        'Subtotal': subtotal,
        'Discount_Amount': discount_amount,
        'Delivery_Fee': delivery_fee,
        'Total_Amount': final_total,
        'Status': 'Completed',
        'Order_Date': datetime.now().isoformat(),
        'Payment_Method': payment_method,
        'Amount_Received': amount_received,
        'Print_Size': print_size
    }
    
    success = sheets_manager.add_order(order_data)

    if success:
        save_session_cart([])
        return jsonify({'success': True, 'order_id': order_id, 'total': final_total})
    return api_error('Failed to process order', 500)


@app.route('/api/email-invoice', methods=['POST'])
def email_invoice():
    data = get_json_body()
    
    if not EMAIL_ADDRESS or not EMAIL_PASSWORD:
        return api_error('Email service not configured', 503)
    
    try:
        # Extract data
        recipient_email = str(data.get('email', '')).strip()
        subject = str(data.get('subject', 'Invoice')).strip()
        message_body = str(data.get('message', '')).strip()
        order_data = data.get('order_data', {})
        company_info = data.get('company_info', {})
        
        if not recipient_email or '@' not in recipient_email:
            return api_error('Valid recipient email is required', 400)

        safe_message_body = html.escape(message_body)
        
        # Generate invoice HTML
        invoice_html = generate_invoice_html(order_data, company_info)
        
        # Create email
        msg = MIMEMultipart('alternative')
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = recipient_email
        msg['Subject'] = subject
        
        # Create text and HTML parts
        text_part = MIMEText(message_body, 'plain')
        html_part = MIMEText(f"""
        <html>
        <body>
            <p>{safe_message_body.replace(chr(10), '<br>')}</p>
            <hr>
            {invoice_html}
        </body>
        </html>
        """, 'html')
        
        msg.attach(text_part)
        msg.attach(html_part)
        
        # Create and attach PDF if possible (optional - requires additional libraries)
        # For now, we'll just send the HTML email
        
        # Send email
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        
        text = msg.as_string()
        server.sendmail(EMAIL_ADDRESS, recipient_email, text)
        server.quit()
        
        return jsonify({'success': True, 'message': 'Invoice sent successfully'})
        
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return api_error('Failed to send email', 500)

@app.route('/api/admin/products/add', methods=['POST'])
@require_admin
def add_product():
    data = get_json_body()
    
    is_valid, error_msg = validate_product_data(data)
    if not is_valid:
        return api_error(error_msg, 400)
    
    product_data = {
        'ID': str(uuid.uuid4())[:8].upper(),
        'Name': str(data.get('name', '')).strip(),
        'Price': safe_float(data.get('price'), 0.0),
        'Stock': safe_int(data.get('stock'), 0),
        'Category': str(data.get('category', '')).strip(),
        'Description': str(data.get('description', '')).strip(),
        'Import_Price': safe_float(data.get('import_price'), 0.0)
    }
    
    success = sheets_manager.add_product(product_data)
    if not success:
        return api_error('Failed to add product', 500)
    return jsonify({'success': True})

@app.route('/api/admin/products/update-stock', methods=['POST'])
@require_admin
def update_stock():
    data = get_json_body()
    product_id = str(data.get('product_id', '')).strip()
    new_stock = data.get('stock')
    reason = str(data.get('reason', 'Manual Update')).strip() or 'Manual Update'
    
    if not product_id or new_stock is None:
        return api_error('Missing required data', 400)
    
    new_stock = safe_int(new_stock, -1)
    if new_stock < 0:
        return api_error('Invalid stock value', 400)
    
    success = sheets_manager.update_product_stock(product_id, new_stock, reason)
    if not success:
        return api_error('Failed to update stock', 500)
    return jsonify({'success': True})

@app.route('/api/admin/products/add-stock', methods=['POST'])
@require_admin
def add_stock():
    data = get_json_body()
    product_id = str(data.get('product_id', '')).strip()
    stock_to_add = data.get('stock_to_add')
    reason = str(data.get('reason', 'Stock Addition')).strip() or 'Stock Addition'
    
    if not product_id or stock_to_add is None:
        return api_error('Missing required data', 400)
    
    stock_to_add = safe_int(stock_to_add, -1)
    if stock_to_add <= 0:
        return api_error('Invalid stock value', 400)
    
    success = sheets_manager.add_stock_to_product(product_id, stock_to_add, reason)
    if not success:
        return api_error('Failed to add stock', 500)
    return jsonify({'success': True})

@app.route('/api/admin/products/update', methods=['POST'])
@require_admin
def update_product():
    data = get_json_body()
    product_id = str(data.get('product_id', '')).strip()
    
    if not product_id:
        return api_error('Product ID is required', 400)
    
    update_data = {}
    if data.get('name'):
        update_data['name'] = str(data['name']).strip()
    if data.get('price') is not None:
        update_data['price'] = safe_float(data['price'], -1.0)
        if update_data['price'] < 0:
            return api_error('Invalid price value', 400)
    
    if data.get('import_price') is not None:
        update_data['import_price'] = safe_float(data['import_price'], -1.0)
        if update_data['import_price'] < 0:
            return api_error('Invalid import price value', 400)
    
    if data.get('category'):
        update_data['category'] = str(data['category']).strip()
    if data.get('description') is not None:
        update_data['description'] = str(data['description']).strip()
    
    if not update_data:
        return api_error('No valid data to update', 400)
    
    success = sheets_manager.update_product(product_id, update_data)
    if not success:
        return api_error('Failed to update product', 500)
    return jsonify({'success': True})

@app.route('/api/admin/orders')
@require_admin
def get_orders():
    orders = sheets_manager.get_orders(50)
    return jsonify(orders)

@app.route('/api/admin/analytics')
@require_admin
def get_analytics():
    orders = sheets_manager.get_orders()
    products = sheets_manager.get_products()
    
    now = datetime.now()
    today_date = now.date()
    week_ago = now - timedelta(days=7)

    today_orders = []
    week_orders = []
    for order in orders:
        order_dt = parse_order_datetime(order.get('Order_Date'))
        if order_dt is None:
            continue
        if order_dt.date() == today_date:
            today_orders.append(order)
        if order_dt >= week_ago:
            week_orders.append(order)
    
    today_revenue = sum(safe_float(o.get('Total_Amount', 0)) for o in today_orders)
    week_revenue = sum(safe_float(o.get('Total_Amount', 0)) for o in week_orders)
    total_revenue = sum(safe_float(o.get('Total_Amount', 0)) for o in orders)

    total_stock_units = 0
    inventory_investment = 0.0
    for product in products:
        stock = max(0, safe_int(product.get('Stock', 0)))
        import_price = max(0.0, safe_float(product.get('Import_Price', 0)))
        total_stock_units += stock
        inventory_investment += stock * import_price
    
    low_stock = sheets_manager.get_low_stock_products()
    
    analytics = {
        'total_orders': len(orders),
        'today_orders': len(today_orders),
        'week_orders': len(week_orders),
        'today_revenue': today_revenue,
        'week_revenue': week_revenue,
        'total_revenue': total_revenue,
        'avg_order_value': total_revenue / len(orders) if orders else 0,
        'total_products': len(products),
        'total_stock_units': total_stock_units,
        'inventory_investment': round(inventory_investment, 2),
        'low_stock_count': len(low_stock),
        'low_stock_products': low_stock
    }
    
    return jsonify(analytics)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
