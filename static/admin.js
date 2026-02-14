class AdminPanel {
    constructor() {
        this.init();
    }

    async init() {
        await this.loadAnalytics();
        await this.loadOrders();
        this.bindEvents();
    }

    bindEvents() {
        document.getElementById('add-product-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.addProduct();
        });
    }

    async loadAnalytics() {
        try {
            const response = await fetch('/api/admin/analytics');
            const analytics = await response.json();
            
            // Fix: Await the getProductCount() method
            const productCount = await this.getProductCount();
            
            document.getElementById('total-products').textContent = productCount;
            document.getElementById('today-orders').textContent = analytics.today_orders;
            document.getElementById('today-revenue').textContent = `$${analytics.today_revenue.toFixed(2)}`;
            document.getElementById('total-revenue').textContent = `$${analytics.total_revenue.toFixed(2)}`;
        } catch (error) {
            console.error('Error loading analytics:', error);
            // Set fallback values if API fails
            document.getElementById('total-products').textContent = '0';
            document.getElementById('today-orders').textContent = '0';
            document.getElementById('today-revenue').textContent = '$0.00';
            document.getElementById('total-revenue').textContent = '$0.00';
        }
    }

    async loadOrders() {
        try {
            const response = await fetch('/api/admin/orders');
            const orders = await response.json();
            this.renderOrders(orders);
        } catch (error) {
            console.error('Error loading orders:', error);
        }
    }

    renderOrders(orders) {
        const ordersList = document.getElementById('orders-list');
        ordersList.innerHTML = '';

        if (orders.length === 0) {
            ordersList.innerHTML = '<p>No orders found</p>';
            return;
        }

        orders.slice(-10).reverse().forEach(order => {
            const orderItem = document.createElement('div');
            orderItem.className = 'order-item';
            
            const orderDate = new Date(order.Order_Date).toLocaleString();
            const items = JSON.parse(order.Items || '[]');
            const itemsText = items.map(item => `${item.name} (${item.quantity})`).join(', ');

            orderItem.innerHTML = `
                <div>
                    <strong>Order: ${order.Order_ID}</strong><br>
                    <strong>Customer:</strong> ${order.Customer_Name}<br>
                    <strong>Items:</strong> ${itemsText}<br>
                    <strong>Total:</strong> $${parseFloat(order.Total_Amount).toFixed(2)}<br>
                    <strong>Payment:</strong> ${order.Payment_Method}<br>
                    <strong>Date:</strong> ${orderDate}
                </div>
            `;
            ordersList.appendChild(orderItem);
        });
    }

    async addProduct() {
        const formData = {
            name: document.getElementById('product-name').value,
            price: document.getElementById('product-price').value,
            stock: document.getElementById('product-stock').value,
            category: document.getElementById('product-category').value,
            description: document.getElementById('product-description').value
        };

        try {
            const response = await fetch('/api/admin/products/add', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(formData)
            });

            const result = await response.json();

            if (result.success) {
                this.showNotification('Product added successfully!', 'success');
                document.getElementById('add-product-form').reset();
                await this.loadAnalytics();
            } else {
                this.showNotification('Failed to add product', 'error');
            }
        } catch (error) {
            console.error('Error adding product:', error);
            this.showNotification('Error adding product', 'error');
        }
    }

    showNotification(message, type = 'success') {
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        notification.textContent = message;
        document.body.appendChild(notification);

        setTimeout(() => {
            notification.classList.add('show');
        }, 100);

        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => {
                document.body.removeChild(notification);
            }, 300);
        }, 3000);
    }
}

// Initialize admin panel
const admin = new AdminPanel();