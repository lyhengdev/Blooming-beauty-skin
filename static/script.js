class POSSystem {
    constructor() {
        this.cart = [];
        this.products = [];
        this.categories = {};
        this.isOnline = navigator.onLine;
        this.lastOrder = null; // Store last order for invoice generation
        this.companyInfo = {
            name: "Blooming Beauty Skin",
            address: "Skincare Studio",
            city: "Phnom Penh",
            phone: "+855 00 000 000",
            email: "care@bloomingbeautyskin.com",
            website: "www.bloomingbeautyskin.com"
        };
        this.init();
    }

    async init() {
        this.setupOfflineHandling();
        await Promise.all([this.loadProducts(), this.loadCart()]);
        this.bindEvents();
        this.renderProducts();
        this.updateCartCount();
        this.setupKeyboardShortcuts();
        this.setupChangeCalculation();
    }

    escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    escapeJsString(value) {
        return String(value ?? '')
            .replace(/\\/g, '\\\\')
            .replace(/'/g, "\\'");
    }

    setCheckoutTotal(total) {
        const formatted = (Number(total) || 0).toFixed(2);
        const checkoutTotal = document.getElementById('checkout-total');
        const checkoutTotalConfirm = document.getElementById('checkout-total-confirm');
        if (checkoutTotal) {
            checkoutTotal.textContent = formatted;
        }
        if (checkoutTotalConfirm) {
            checkoutTotalConfirm.textContent = formatted;
        }
    }

    setupOfflineHandling() {
        window.addEventListener('online', () => {
            this.isOnline = true;
            this.showNotification('Connection restored', 'success');
            this.syncOfflineData();
        });

        window.addEventListener('offline', () => {
            this.isOnline = false;
            this.showNotification('Working offline', 'warning');
        });
    }

    setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey || e.metaKey) {
                switch(e.key) {
                    case 'k':
                        e.preventDefault();
                        document.getElementById('search-input').focus();
                        break;
                    case 'b':
                        e.preventDefault();
                        this.showCart();
                        break;
                    case 'n':
                        e.preventDefault();
                        this.clearCart();
                        break;
                    case 'p':
                        e.preventDefault();
                        if (this.lastOrder) {
                            this.printInvoice();
                        }
                        break;
                }
            }
        });
    }

    setupChangeCalculation() {
        const amountReceived = document.getElementById('amount-received');
        const changeAmount = document.getElementById('change-amount');
        const discountAmount = document.getElementById('discount-amount');
        const deliveryFee = document.getElementById('delivery-fee');
        
        const updateTotals = () => {
            const subtotal = parseFloat(document.getElementById('checkout-subtotal').textContent) || 0;
            const discount = parseFloat(discountAmount.value) || 0;
            const delivery = parseFloat(deliveryFee.value) || 0;
            const finalTotal = Math.max(0, subtotal - discount + delivery);
           
            
            document.getElementById('checkout-discount').textContent = discount.toFixed(2);
            document.getElementById('checkout-delivery').textContent = delivery.toFixed(2);
            this.setCheckoutTotal(finalTotal);
            
            const received = parseFloat(amountReceived.value) || 0;
            const change = received - finalTotal;
            
            if (received > 0) {
                if (change >= 0) {
                    changeAmount.textContent = `Change: $${change.toFixed(2)}`;
                    changeAmount.className = 'change-display positive';
                } else {
                    changeAmount.textContent = `Remaining: $${Math.abs(change).toFixed(2)}`;
                    changeAmount.className = 'change-display negative';
                }
            } else {
                changeAmount.textContent = '';
                changeAmount.className = 'change-display';
            }
        };
        
        discountAmount.addEventListener('input', updateTotals);
        deliveryFee.addEventListener('input', updateTotals);
        amountReceived.addEventListener('input', updateTotals);
    }

    async loadProducts() {
        try {
            this.showLoading(true);
            const response = await fetch('/api/products');
            if (!response.ok) throw new Error('Failed to load products');
            this.products = await response.json();
            localStorage.setItem('cached_products', JSON.stringify(this.products));
            this.loadCategories();
        } catch (error) {
            console.error('Error loading products:', error);
            this.showNotification('Failed to load products', 'error');
            const cached = localStorage.getItem('cached_products');
            if (cached) {
                this.products = JSON.parse(cached);
                this.showNotification('Loaded cached products', 'warning');
                this.loadCategories();
            }
        } finally {
            this.showLoading(false);
        }
    }

    loadCategories() {
        const categories = {};
        this.products.forEach(product => {
            const category = String(product.Category || 'General').trim() || 'General';
            categories[category] = (categories[category] || 0) + 1;
        });
        this.categories = categories;
        this.renderCategories();
    }

    async loadCart() {
        try {
            const response = await fetch('/api/cart');
            if (response.ok) {
                this.cart = await response.json();
            }
        } catch (error) {
            console.error('Error loading cart:', error);
            const cached = localStorage.getItem('cart');
            if (cached) {
                this.cart = JSON.parse(cached);
            }
        }
    }

    bindEvents() {
        // Search with debounce
        let searchTimeout;
        document.getElementById('search-input').addEventListener('input', (e) => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                if (e.target.value.trim()) {
                    this.searchProducts();
                } else {
                    this.renderProducts();
                    document.getElementById('products-title').textContent = 'All Products';
                }
            }, 300);
        });

        document.getElementById('search-btn').addEventListener('click', () => this.searchProducts());
        
        document.getElementById('search-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter') this.searchProducts();
        });

        // Categories
        document.getElementById('categories-btn').addEventListener('click', () => this.toggleCategories());

        // Cart
        document.getElementById('cart-btn').addEventListener('click', () => this.showCart());

        // Modals
        document.querySelectorAll('.close').forEach(close => {
            close.addEventListener('click', (e) => {
                e.target.closest('.modal').classList.add('hidden');
            });
        });

        // Close modal on backdrop click
        document.querySelectorAll('.modal').forEach(modal => {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) {
                    modal.classList.add('hidden');
                }
            });
        });

        // Checkout form
        document.getElementById('checkout-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.checkout();
        });

        // Clear cart
        document.getElementById('clear-cart-btn').addEventListener('click', () => {
            if (confirm('Are you sure you want to clear the cart?')) {
                this.clearCart();
            }
        });

        // Invoice actions
        document.getElementById('print-invoice-btn').addEventListener('click', () => this.printInvoice());
        document.getElementById('download-invoice-btn').addEventListener('click', () => this.downloadInvoice());
        document.getElementById('email-invoice-btn').addEventListener('click', () => this.showEmailModal());

        // Email form
        document.getElementById('email-form').addEventListener('submit', (e) => {
            e.preventDefault();
            this.emailInvoice();
        });

        // Barcode scanner simulation
        document.getElementById('search-input').addEventListener('keypress', (e) => {
            if (e.key === 'Enter' && e.target.value.length > 8) {
                this.searchByBarcode(e.target.value);
            }
        });
    }

    searchByBarcode(barcode) {
        const product = this.products.find(p => p.ID === barcode || String(p.Name || '').toLowerCase().includes(barcode.toLowerCase()));
        if (product) {
            this.addToCart(product.ID, null, 1);
            document.getElementById('search-input').value = '';
        } else {
            this.showNotification('Product not found', 'error');
        }
    }

    showLoading(show) {
        const loader = document.getElementById('loading');
        if (loader) {
            loader.style.display = show ? 'block' : 'none';
        }
    }

    renderProducts(products = this.products) {
        const grid = document.getElementById('products-grid');
        grid.innerHTML = '';

        if (products.length === 0) {
            grid.innerHTML = '<div class="no-products">No products found</div>';
            return;
        }

        products.forEach(product => {
            const productCard = this.createProductCard(product);
            grid.appendChild(productCard);
        });
    }

    createProductCard(product) {
        const card = document.createElement('div');
        card.className = 'product-card';
        card.setAttribute('data-product-id', product.ID);

        const stockClass = product.Stock > 10 ? 'stock-high' : product.Stock > 0 ? 'stock-medium' : 'stock-low';
        const stockText = product.Stock > 0 ? `${product.Stock} in stock` : 'Out of stock';
        const safeName = this.escapeHtml(product.Name);
        const safeId = this.escapeHtml(product.ID);
        const safeCategory = this.escapeHtml(product.Category);
        const safeDescription = this.escapeHtml(product.Description || '');
        const productIdForJs = this.escapeJsString(product.ID);

        card.innerHTML = `
            <div class="product-header">
                <h4 title="${safeName}">${safeName}</h4>
                <div class="product-id">ID: ${safeId}</div>
            </div>
            <div class="product-price">$${parseFloat(product.Price).toFixed(2)}</div>
            <div class="product-stock ${stockClass}">${stockText}</div>
            <div class="product-category">${safeCategory}</div>
            <div class="product-description" title="${safeDescription}">${safeDescription}</div>
            <div class="product-actions">
                <div class="quantity-control">
                    <button class="qty-btn minus" onclick="pos.changeQuantity(this, -1)" ${product.Stock === 0 ? 'disabled' : ''}>-</button>
                    <input type="number" class="quantity-input" min="1" max="${product.Stock}" value="1" ${product.Stock === 0 ? 'disabled' : ''}>
                    <button class="qty-btn plus" onclick="pos.changeQuantity(this, 1)" ${product.Stock === 0 ? 'disabled' : ''}>+</button>
                </div>
                <button class="btn btn-primary add-to-cart-btn" onclick="pos.addToCart('${productIdForJs}', this)" ${product.Stock === 0 ? 'disabled' : ''}>
                    ${product.Stock === 0 ? 'Out of Stock' : 'Add to Cart'}
                </button>
            </div>
        `;

        return card;
    }

    changeQuantity(button, change) {
        const input = button.parentElement.querySelector('.quantity-input');
        const currentValue = parseInt(input.value);
        const newValue = Math.max(1, Math.min(input.max, currentValue + change));
        input.value = newValue;
    }

    renderCategories() {
        const categoriesSection = document.getElementById('categories-section');
        const categoriesList = document.getElementById('categories-list');
        
        categoriesList.innerHTML = '';
        
        const allBtn = document.createElement('div');
        allBtn.className = 'category-btn';
        allBtn.textContent = `All Products (${this.products.length})`;
        allBtn.addEventListener('click', () => {
            this.renderProducts();
            document.getElementById('products-title').textContent = 'All Products';
            this.hideCategories();
        });
        categoriesList.appendChild(allBtn);
        
        Object.entries(this.categories).forEach(([category, count]) => {
            const categoryBtn = document.createElement('div');
            categoryBtn.className = 'category-btn';
            categoryBtn.textContent = `${category} (${count})`;
            categoryBtn.addEventListener('click', () => this.filterByCategory(category));
            categoriesList.appendChild(categoryBtn);
        });
    }

    searchProducts() {
        const query = document.getElementById('search-input').value;
        if (!query.trim()) {
            this.renderProducts();
            document.getElementById('products-title').textContent = 'All Products';
            return;
        }

        const queryLower = query.toLowerCase();
        const filtered = this.products.filter(product =>
            String(product.Name || '').toLowerCase().includes(queryLower) ||
            String(product.Category || '').toLowerCase().includes(queryLower) ||
            String(product.Description || '').toLowerCase().includes(queryLower)
        );
        this.renderProducts(filtered);
        document.getElementById('products-title').textContent = `Search Results for "${query}" (${filtered.length} found)`;
    }

    filterByCategory(category) {
        const filtered = this.products.filter(
            p => String(p.Category || 'General').toLowerCase() === String(category).toLowerCase()
        );
        this.renderProducts(filtered);
        document.getElementById('products-title').textContent = `${category} (${filtered.length} products)`;
        this.hideCategories();
    }

    toggleCategories() {
        const categoriesSection = document.getElementById('categories-section');
        categoriesSection.classList.toggle('hidden');
    }

    hideCategories() {
        document.getElementById('categories-section').classList.add('hidden');
    }

    async addToCart(productId, buttonElement, quantity = null) {
        const productCard = buttonElement ? buttonElement.closest('.product-card') : 
                          document.querySelector(`[data-product-id="${productId}"]`);
        const quantityInput = productCard?.querySelector('.quantity-input');
        const qty = quantity || (quantityInput ? parseInt(quantityInput.value) : 1);

        if (buttonElement) {
            buttonElement.disabled = true;
            buttonElement.textContent = 'Adding...';
        }

        try {
            const response = await fetch('/api/cart/add', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    product_id: productId,
                    quantity: qty
                })
            });

            const result = await response.json();

            if (result.success) {
                if (Array.isArray(result.cart)) {
                    this.cart = result.cart;
                } else {
                    await this.loadCart();
                }
                this.updateCartCount();
                this.saveCartToStorage();
                this.showNotification(`Added ${qty} item(s) to cart!`, 'success');
                
                if (quantityInput) quantityInput.value = 1;
            } else {
                this.showNotification(result.message, 'error');
            }
        } catch (error) {
            console.error('Error adding to cart:', error);
            this.showNotification('Failed to add product to cart', 'error');
        } finally {
            if (buttonElement) {
                buttonElement.disabled = false;
                buttonElement.textContent = 'Add to Cart';
            }
        }
    }

    updateCartCount() {
        const totalItems = this.cart.reduce((sum, item) => sum + item.quantity, 0);
        const cartCount = document.getElementById('cart-count');
        cartCount.textContent = totalItems;
        
        if (totalItems > 0) {
            cartCount.parentElement.classList.add('cart-has-items');
        } else {
            cartCount.parentElement.classList.remove('cart-has-items');
        }
    }

    saveCartToStorage() {
        localStorage.setItem('cart', JSON.stringify(this.cart));
    }

    showCart() {
        this.renderCart();
        document.getElementById('cart-modal').classList.remove('hidden');
        document.getElementById('cart-modal').classList.add('show');
    }

    renderCart() {
        const cartItems = document.getElementById('cart-items');
        const cartTotal = document.getElementById('cart-total');
    
        if (this.cart.length === 0) {
            cartItems.innerHTML = '<div class="empty-cart">Your cart is empty<br><small>Add products to get started.</small></div>';
            cartTotal.textContent = '0.00';
            document.getElementById('checkout-subtotal').textContent = '0.00';
            this.setCheckoutTotal(0);
            return;
        }
    
        cartItems.innerHTML = '';
        let total = 0;
    
        this.cart.forEach((item, index) => {
            const cartItem = document.createElement('div');
            cartItem.className = 'cart-item';
            const safeName = this.escapeHtml(item.name);
            const productIdForJs = this.escapeJsString(item.product_id);
            cartItem.innerHTML = `
                <div class="cart-item-info">
                    <strong class="item-name">${safeName}</strong>
                    <div class="item-price">$${parseFloat(item.unit_price).toFixed(2)} each</div>
                </div>
                <div class="cart-item-controls">
                    <div class="quantity-control">
                        <button class="qty-btn minus" onclick="pos.updateCartQuantity('${productIdForJs}', ${item.quantity - 1})">-</button>
                        <span class="qty-display">${item.quantity}</span>
                        <button class="qty-btn plus" onclick="pos.updateCartQuantity('${productIdForJs}', ${item.quantity + 1})">+</button>
                    </div>
                    <div class="item-total">$${parseFloat(item.total_price).toFixed(2)}</div>
                    <button class="btn btn-danger btn-small" onclick="pos.removeFromCart('${productIdForJs}')" title="Remove item">🗑️</button>
                </div>
            `;
            cartItems.appendChild(cartItem);
            total += item.total_price;
        });
    
        cartTotal.textContent = total.toFixed(2);
        document.getElementById('checkout-subtotal').textContent = total.toFixed(2);
        this.setCheckoutTotal(total);
    }

    async updateCartQuantity(productId, newQuantity) {
        if (newQuantity <= 0) {
            this.removeFromCart(productId);
            return;
        }

        try {
            const response = await fetch('/api/cart/update', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    product_id: productId,
                    quantity: newQuantity
                })
            });

            const result = await response.json();
            if (!response.ok || !result.success) {
                this.showNotification(result.message || 'Failed to update cart', 'error');
                return;
            }

            if (Array.isArray(result.cart)) {
                this.cart = result.cart;
            } else {
                await this.loadCart();
            }
            this.updateCartCount();
            this.renderCart();
            this.saveCartToStorage();
        } catch (error) {
            console.error('Error updating cart:', error);
            this.showNotification('Error updating cart', 'error');
        }
    }

    async removeFromCart(productId) {
        try {
            const response = await fetch('/api/cart/remove', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    product_id: productId
                })
            });

            const result = await response.json();
            if (!response.ok || !result.success) {
                this.showNotification(result.message || 'Failed to remove item', 'error');
                return;
            }

            if (Array.isArray(result.cart)) {
                this.cart = result.cart;
            } else {
                await this.loadCart();
            }
            this.updateCartCount();
            this.renderCart();
            this.saveCartToStorage();
            this.showNotification('Item removed from cart', 'success');
        } catch (error) {
            console.error('Error removing from cart:', error);
            this.showNotification('Error removing item', 'error');
        }
    }

    async clearCart() {
        try {
            const response = await fetch('/api/cart/clear', {
                method: 'POST'
            });

            const result = await response.json();
            if (!response.ok || !result.success) {
                this.showNotification(result.message || 'Failed to clear cart', 'error');
                return;
            }

            this.cart = Array.isArray(result.cart) ? result.cart : [];
            this.updateCartCount();
            this.renderCart();
            this.saveCartToStorage();
            this.showNotification('Cart cleared', 'success');
        } catch (error) {
            console.error('Error clearing cart:', error);
            this.showNotification('Error clearing cart', 'error');
        }
    }

async checkout() {
    if (this.cart.length === 0) {
        this.showNotification('Cart is empty', 'error');
        return;
    }

    const customerName = document.getElementById('customer-name').value.trim() || 'Walk-in Customer';
    const customerPhone = document.getElementById('customer-phone').value.trim() || '';
    const customerAddress = document.getElementById('customer-address').value.trim() || '';
    const discountAmount = parseFloat(document.getElementById('discount-amount').value) || 0;
    const deliveryFee = parseFloat(document.getElementById('delivery-fee').value) || 0;
    const printSize = document.getElementById('print-size').value;

    const paymentMethod = document.getElementById('payment-method').value;
    const amountReceived = parseFloat(document.getElementById('amount-received').value) || 0;
    const subtotal = parseFloat(document.getElementById('checkout-subtotal').textContent) || 0;
    const finalTotal = Math.max(0, subtotal - discountAmount + deliveryFee);
    const submitBtn = document.querySelector('#checkout-form button[type="submit"]');

    if (paymentMethod === 'Cash' && amountReceived < finalTotal) {
        this.showNotification('Amount received is less than total', 'error');
        return;
    }

    submitBtn.disabled = true;
    submitBtn.textContent = 'Processing...';

    try {
        const response = await fetch('/api/checkout', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                customer_name: customerName,
                customer_phone: customerPhone,
                customer_address: customerAddress,
                discount_amount: discountAmount,
                delivery_fee: deliveryFee,
                payment_method: paymentMethod,
                amount_received: amountReceived,
                print_size: printSize
            })
        });

        const result = await response.json();

        if (result.success) {
            this.lastOrder = {
                order_id: result.order_id,
                subtotal: subtotal,
                discount_amount: discountAmount,
                delivery_fee: deliveryFee,
                total: finalTotal,
                items: [...this.cart],
                customer_name: customerName,
                customer_phone: customerPhone,
                customer_address: customerAddress,
                payment_method: paymentMethod,
                amount_received: amountReceived,
                change: amountReceived > finalTotal ? amountReceived - finalTotal : 0,
                date: new Date().toISOString(),
                invoice_number: `INV-${result.order_id.split('-').slice(-2).join('-')}`,
                print_size: printSize
            };

            this.cart = [];
            this.updateCartCount();
            this.renderCart();
            this.saveCartToStorage();
            
            document.getElementById('cart-modal').classList.add('hidden');
            document.getElementById('checkout-modal').classList.add('hidden');
            
            this.showOrderSuccess(result.order_id, finalTotal);
            
            document.getElementById('checkout-form').reset();
            document.getElementById('checkout-discount').textContent = '0.00';
            document.getElementById('checkout-delivery').textContent = '0.00';
            this.setCheckoutTotal(0);
            document.getElementById('change-amount').textContent = '';
        } else {
            this.showNotification(result.message, 'error');
        }
    } catch (error) {
        console.error('Error during checkout:', error);
        this.showNotification('Checkout failed. Please try again.', 'error');
    } finally {
        submitBtn.disabled = false;
        submitBtn.textContent = 'Complete Order';
    }
	}
    showOrderSuccess(orderId, total) {
        const safeOrderId = this.escapeHtml(orderId);
        const modal = document.createElement('div');
        modal.className = 'modal show';
        modal.innerHTML = `
            <div class="modal-content success-modal">
                <div class="success-icon">✅</div>
                <h2>Order Completed!</h2>
                <div class="order-details">
                    <p><strong>Order ID:</strong> ${safeOrderId}</p>
                    <p><strong>Total:</strong> $${total.toFixed(2)}</p>
                </div>
                <div class="success-actions">
                    <button class="btn btn-primary" onclick="pos.showInvoice(); this.parentElement.parentElement.parentElement.remove();">View Invoice</button>
                    <button class="btn btn-secondary" onclick="this.parentElement.parentElement.parentElement.remove()">Continue Shopping</button>
                </div>
            </div>
        `;
        document.body.appendChild(modal);

        // Auto-remove after 10 seconds if no action
        setTimeout(() => {
            if (modal.parentElement) {
                modal.remove();
            }
        }, 10000);
    }

    showInvoice() {
        if (!this.lastOrder) {
            this.showNotification('No recent order found', 'error');
            return;
        }

        this.generateInvoiceHTML();
        document.getElementById('invoice-modal').classList.remove('hidden');
        document.getElementById('invoice-modal').classList.add('show');
    }

generateInvoiceHTML() {
    const order = this.lastOrder;
    const invoiceContent = document.getElementById('invoice-content');
    const safeOrder = {
        ...order,
        invoice_number: this.escapeHtml(order.invoice_number),
        order_id: this.escapeHtml(order.order_id),
        customer_name: this.escapeHtml(order.customer_name),
        customer_phone: this.escapeHtml(order.customer_phone || ''),
        customer_address: this.escapeHtml(order.customer_address || ''),
        payment_method: this.escapeHtml(order.payment_method || '')
    };
    const safeCompany = {
        name: this.escapeHtml(this.companyInfo.name),
        address: this.escapeHtml(this.companyInfo.address),
        city: this.escapeHtml(this.companyInfo.city),
        phone: this.escapeHtml(this.companyInfo.phone),
        email: this.escapeHtml(this.companyInfo.email)
    };
    const itemsHtml = (order.items || []).map(item => `
        <tr>
            <td>${this.escapeHtml(item.name)}</td>
            <td>${parseInt(item.quantity, 10) || 0}</td>
            <td>$${parseFloat(item.unit_price).toFixed(2)}</td>
            <td>$${parseFloat(item.total_price).toFixed(2)}</td>
        </tr>
    `).join('');
    
    const invoiceHTML = `
        <div class="invoice-header-section">
            <div class="company-info">
                <h1>${safeCompany.name}</h1>
                <p>${safeCompany.address}</p>
                <p>${safeCompany.city}</p>
                <p>Phone: ${safeCompany.phone}</p>
                <p>Email: ${safeCompany.email}</p>
            </div>
            <div class="invoice-details">
                <h2>INVOICE</h2>
                <p><strong>Invoice #:</strong> ${safeOrder.invoice_number}</p>
                <p><strong>Order ID:</strong> ${safeOrder.order_id}</p>
                <p><strong>Date:</strong> ${new Date(order.date).toLocaleDateString()}</p>
                <p><strong>Time:</strong> ${new Date(order.date).toLocaleTimeString()}</p>
            </div>
        </div>

        <div class="customer-info">
            <h3>Bill To:</h3>
            <p><strong>${safeOrder.customer_name}</strong></p>
            ${safeOrder.customer_phone ? `<p>Phone: ${safeOrder.customer_phone}</p>` : ''}
            ${safeOrder.customer_address ? `<p>Address: ${safeOrder.customer_address}</p>` : ''}
        </div>

        <div class="invoice-items">
            <table class="invoice-table">
                <thead>
                    <tr>
                        <th>Item</th>
                        <th>Qty</th>
                        <th>Unit Price</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                    ${itemsHtml}
                </tbody>
            </table>
        </div>

        <div class="invoice-summary">
            <div class="summary-row">
                <span>Subtotal:</span>
                <span>$${order.subtotal.toFixed(2)}</span>
            </div>
            ${order.discount_amount > 0 ? `
            <div class="summary-row discount-row">
                <span>Discount:</span>
                <span>-$${order.discount_amount.toFixed(2)}</span>
            </div>
            ` : ''}
            ${order.delivery_fee > 0 ? `
            <div class="summary-row delivery-row">
                <span>Delivery Fee:</span>
                <span>$${order.delivery_fee.toFixed(2)}</span>
            </div>
            ` : ''}
            <div class="summary-row">
                <span>Tax (0%):</span>
                <span>$0.00</span>
            </div>
            <div class="summary-row total-row">
                <span><strong>FINAL TOTAL:</strong></span>
                <span><strong>$${order.total.toFixed(2)}</strong></span>
            </div>
            ${order.payment_method === 'Cash' ? `
                <div class="payment-info">
                    <div class="summary-row">
                        <span>Amount Received:</span>
                        <span>$${order.amount_received.toFixed(2)}</span>
                    </div>
                    <div class="summary-row">
                        <span>Change:</span>
                        <span>$${order.change.toFixed(2)}</span>
                    </div>
                </div>
            ` : ''}
        </div>

        <div class="payment-method">
            <p><strong>Payment Method:</strong> ${safeOrder.payment_method}</p>
        </div>

        <div class="invoice-footer-text">
            <p>Thank you for your business!</p>
            <p>Questions? Contact us at ${safeCompany.phone} or ${safeCompany.email}</p>
        </div>
    `;

    invoiceContent.innerHTML = invoiceHTML;
}

    printInvoice() {
        if (!this.lastOrder) {
            this.showNotification('No invoice to print', 'error'); 
            return;
        }

        const printFrame = document.getElementById('print-frame');
        const printContent = this.generatePrintableInvoice();
        
        printFrame.contentDocument.open();
        printFrame.contentDocument.write(printContent);
        printFrame.contentDocument.close();
        
        setTimeout(() => {
            printFrame.contentWindow.print();
        }, 500);
    }

    generatePrintableInvoice() {
        const order = this.lastOrder;
        const printSize = order.print_size || '80mm';
        const safeOrder = {
            invoice_number: this.escapeHtml(order.invoice_number),
            order_id: this.escapeHtml(order.order_id),
            customer_name: this.escapeHtml(order.customer_name),
            customer_phone: this.escapeHtml(order.customer_phone || ''),
            customer_address: this.escapeHtml(order.customer_address || ''),
            payment_method: this.escapeHtml(order.payment_method || '')
        };
        const safeCompany = {
            name: this.escapeHtml(this.companyInfo.name),
            address: this.escapeHtml(this.companyInfo.address),
            city: this.escapeHtml(this.companyInfo.city),
            phone: this.escapeHtml(this.companyInfo.phone),
            email: this.escapeHtml(this.companyInfo.email),
            website: this.escapeHtml(this.companyInfo.website)
        };
        const printableItems = (order.items || []).map(item => `
                        <tr>
                            <td>${this.escapeHtml(item.name)}</td>
                            <td>${parseInt(item.quantity, 10) || 0}</td>
                            <td>$${parseFloat(item.unit_price).toFixed(2)}</td>
                            <td>$${parseFloat(item.total_price).toFixed(2)}</td>
                        </tr>
                    `).join('');
        
        let pageSize = '';
        let bodyWidth = '72mm';
        let fontSize = '12px';
        let padding = '5px';
        
        switch(printSize) {
            case '80mm':
                pageSize = 'size: 80mm auto; margin: 2mm;';
                bodyWidth = '72mm';
                fontSize = '10px';
                padding = '3px';
                break;
            case '100mm':
                pageSize = 'size: 100mm auto; margin: 3mm;';
                bodyWidth = '92mm';
                fontSize = '11px';
                padding = '5px';
                break;
            case 'A4':
                pageSize = 'size: A4; margin: 20mm;';
                bodyWidth = '170mm';
                fontSize = '12px';
                padding = '15px';
                break;
            case 'A5':
                pageSize = 'size: A5; margin: 15mm;';
                bodyWidth = '118mm';
                fontSize = '11px';
                padding = '10px';
                break;
            case 'letter':
                pageSize = 'size: letter; margin: 1in;';
                bodyWidth = '6.5in';
                fontSize = '12px';
                padding = '15px';
                break;
        }
        
        return `
        <!DOCTYPE html>
        <html>
        <head>
            <title>Invoice - ${safeOrder.invoice_number}</title>
            <style>
                @page {
                    ${pageSize}
                }
                body { 
                    font-family: 'Arial', sans-serif;
                    font-size: ${fontSize};
                    line-height: 1.4;
                    margin: 0;
                    padding: ${padding};
                    width: ${bodyWidth};
                    color: #2d1f2d;
                    background: #fffafe;
                }
                
                .invoice-header-section {
                    display: flex;
                    justify-content: space-between;
                    margin-bottom: 20px;
                    padding-bottom: 10px;
                    border-bottom: 2px solid #d8bfd3;
                    page-break-inside: avoid;
                }
                
                .company-info h1 {
                    margin: 0 0 8px 0;
                    font-size: 1.6em;
                    color: #6f2f59;
                    font-weight: bold;
                }
                
                .company-info p {
                    margin: 2px 0;
                    font-size: 0.9em;
                }
                
                .invoice-details {
                    text-align: right;
                }
                
                .invoice-details h2 {
                    margin: 0 0 8px 0;
                    font-size: 1.8em;
                    color: #b1457f;
                    font-weight: bold;
                }
                
                .invoice-details p {
                    margin: 3px 0;
                    font-size: 0.9em;
                }
                
                .customer-info {
                    margin-bottom: 20px;
                    padding: 10px;
                    background-color: #fff3fa;
                    border-left: 4px solid #a85c96;
                    page-break-inside: avoid;
                }
                
                .customer-info h3 {
                    margin: 0 0 8px 0;
                    color: #83496f;
                    font-size: 1.1em;
                }
                
                .customer-info p {
                    margin: 3px 0;
                }
                
                .invoice-table {
                    width: 100%;
                    border-collapse: collapse;
                    margin-bottom: 20px;
                    break-inside: auto;
                }
                
                .invoice-table th,
                .invoice-table td {
                    padding: 8px 5px;
                    text-align: left;
                    border-bottom: 1px solid #ead7e7;
                    font-size: 0.9em;
                }
                
                .invoice-table th {
                    background-color: #fff1f8;
                    font-weight: bold;
                    color: #5f4458;
                }
                
                .invoice-table th:last-child,
                .invoice-table td:last-child {
                    text-align: right;
                }
                
                .invoice-summary {
                    margin-top: 20px;
                    width: 100%;
                    page-break-inside: avoid;
                }
                
                .summary-row {
                    display: flex;
                    justify-content: space-between;
                    padding: 5px 0;
                    border-bottom: 1px solid #f0ddea;
                }
                
                .summary-row.discount-row {
                    color: #8a3f84;
                    font-weight: bold;
                }
                
                .summary-row.delivery-row {
                    color: #7a4ea0;
                    font-weight: bold;
                }
                
                .summary-row.total-row {
                    font-weight: bold;
                    font-size: 1.1em;
                    border-top: 2px solid #d8bfd3;
                    border-bottom: 2px solid #d8bfd3;
                    background-color: #fff3fa;
                    padding: 10px 0;
                    margin-top: 5px;
                }
                
                .payment-info {
                    margin-top: 10px;
                    padding-top: 10px;
                    border-top: 1px solid #ead7e7;
                }
                
                .payment-method {
                    margin-top: 20px;
                    padding: 10px;
                    background-color: #f9eff7;
                    border-radius: 3px;
                    border-left: 4px solid #a85c96;
                    page-break-inside: avoid;
                }
                
                .invoice-footer-text {
                    text-align: center;
                    margin-top: 30px;
                    padding-top: 15px;
                    border-top: 1px solid #e5cfe1;
                    color: #6f5368;
                    page-break-inside: avoid;
                }
                
                /* Hide elements that shouldn't print */
                @media print {
                    body * {
                        visibility: visible;
                    }
                    
                    .modal,
                    .close,
                    .invoice-actions,
                    .invoice-header,
                    .invoice-footer {
                        display: none !important;
                    }
                }
            </style>
        </head>
        <body>
            <div class="invoice-header-section">
                <div class="company-info">
                    <h1>${safeCompany.name}</h1>
                    <p>${safeCompany.address}</p>
                    <p>${safeCompany.city}</p>
                    <p>Phone: ${safeCompany.phone}</p>
                    <p>Email: ${safeCompany.email}</p>
                </div>
                <div class="invoice-details">
                    <h2>INVOICE</h2>
                    <p><strong>Invoice #:</strong> ${safeOrder.invoice_number}</p>
                    <p><strong>Order ID:</strong> ${safeOrder.order_id}</p>
                    <p><strong>Date:</strong> ${new Date(order.date).toLocaleDateString()}</p>
                    <p><strong>Time:</strong> ${new Date(order.date).toLocaleTimeString()}</p>
                </div>
            </div>
    
            <div class="customer-info">
                <h3>Bill To:</h3>
                <p><strong>${safeOrder.customer_name}</strong></p>
                ${safeOrder.customer_phone ? `<p>Phone: ${safeOrder.customer_phone}</p>` : ''}
                ${safeOrder.customer_address ? `<p>Address: ${safeOrder.customer_address}</p>` : ''}
            </div>
    
            <table class="invoice-table">
                <thead>
                    <tr>
                        <th>Item</th>
                        <th>Qty</th>
                        <th>Unit Price</th>
                        <th>Total</th>
                    </tr>
                </thead>
                <tbody>
                    ${printableItems}
                </tbody>
            </table>
    
            <div class="invoice-summary">
                <div class="summary-row">
                    <span>Subtotal:</span>
                    <span>$${order.subtotal.toFixed(2)}</span>
                </div>
                ${order.discount_amount > 0 ? `
                <div class="summary-row discount-row">
                    <span>Discount:</span>
                    <span>-$${order.discount_amount.toFixed(2)}</span>
                </div>
                ` : ''}
                ${order.delivery_fee > 0 ? `
                <div class="summary-row delivery-row">
                    <span>Delivery Fee:</span>
                    <span>$${order.delivery_fee.toFixed(2)}</span>
                </div>
                ` : ''}
                <div class="summary-row">
                    <span>Tax (0%):</span>
                    <span>$0.00</span>
                </div>
                <div class="summary-row total-row">
                    <span>FINAL TOTAL:</span>
                    <span>$${order.total.toFixed(2)}</span>
                </div>
                ${order.payment_method === 'Cash' ? `
                    <div class="payment-info">
                        <div class="summary-row">
                            <span>Amount Received:</span>
                            <span>$${order.amount_received.toFixed(2)}</span>
                        </div>
                        <div class="summary-row">
                            <span>Change:</span>
                            <span>$${order.change.toFixed(2)}</span>
                        </div>
                    </div>
                ` : ''}
            </div>
    
            <div class="payment-method">
                <p><strong>Payment Method:</strong> ${safeOrder.payment_method}</p>
            </div>
    
            <div class="invoice-footer-text">
                <p><strong>Thank you for your business!</strong></p>
                <p>Questions? Contact us at ${safeCompany.phone} or ${safeCompany.email}</p>
                <p>Website: ${safeCompany.website}</p>
            </div>
        </body>
        </html>
        `;
    }
    downloadInvoice() {
        if (!this.lastOrder) {
            this.showNotification('No invoice to download', 'error');
            return;
        }

        const printContent = this.generatePrintableInvoice();
        const blob = new Blob([printContent], { type: 'text/html' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const safeInvoiceFile = String(this.lastOrder.invoice_number || 'invoice').replace(/[^a-zA-Z0-9_-]/g, '_');
        a.download = `invoice_${safeInvoiceFile}.html`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        
        this.showNotification('Invoice downloaded successfully!', 'success');
    }

    showEmailModal() {
        if (!this.lastOrder) {
            this.showNotification('No invoice to email', 'error');
            return;
        }

        document.getElementById('invoice-modal').classList.add('hidden');
        document.getElementById('email-modal').classList.remove('hidden');
        document.getElementById('email-modal').classList.add('show');
        
        // Pre-fill subject
        document.getElementById('email-subject').value = `Invoice ${this.lastOrder.invoice_number} - ${this.companyInfo.name}`;
    }

    async emailInvoice() {
        const emailAddress = document.getElementById('email-address').value.trim();
        const subject = document.getElementById('email-subject').value.trim();
        const message = document.getElementById('email-message').value.trim();
        const submitBtn = document.querySelector('#email-form button[type="submit"]');

        if (!emailAddress) {
            this.showNotification('Email address is required', 'error');
            return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = 'Sending...';

        try {
            const response = await fetch('/api/email-invoice', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    email: emailAddress,
                    subject: subject,
                    message: message,
                    order_data: this.lastOrder,
                    company_info: this.companyInfo
                })
            });

            const result = await response.json();

            if (result.success) {
                this.showNotification('Invoice sent successfully!', 'success');
                document.getElementById('email-modal').classList.add('hidden');
                document.getElementById('email-form').reset();
            } else {
                this.showNotification(result.message || 'Failed to send email', 'error');
            }
        } catch (error) {
            console.error('Error sending email:', error);
            this.showNotification('Failed to send email', 'error');
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Send Email';
        }
    }

    syncOfflineData() {
        console.log('Syncing offline data...');
    }

    showNotification(message, type = 'success') {
        document.querySelectorAll('.notification').forEach(n => n.remove());

        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        
        const icon = {
            success: '✅',
            error: '❌',
            warning: '⚠️',
            info: 'ℹ️'
        }[type] || '📢';

        const iconSpan = document.createElement('span');
        iconSpan.className = 'notification-icon';
        iconSpan.textContent = icon;

        const messageSpan = document.createElement('span');
        messageSpan.className = 'notification-message';
        messageSpan.textContent = message;

        const closeButton = document.createElement('button');
        closeButton.className = 'notification-close';
        closeButton.textContent = '×';
        closeButton.addEventListener('click', () => notification.remove());

        notification.appendChild(iconSpan);
        notification.appendChild(messageSpan);
        notification.appendChild(closeButton);

        document.body.appendChild(notification);

        setTimeout(() => {
            notification.classList.add('show');
        }, 100);

        setTimeout(() => {
            notification.classList.remove('show');
            setTimeout(() => {
                if (notification.parentElement) {
                    notification.remove();
                }
            }, 300);
        }, 5000);
    }

    quickAddProduct(name, quantity = 1) {
        const product = this.products.find(p => 
            p.Name.toLowerCase().includes(name.toLowerCase())
        );
        if (product) {
            this.addToCart(product.ID, null, quantity);
        } else {
            this.showNotification(`Product "${name}" not found`, 'error');
        }
    }
}

// Initialize the POS system
const pos = new POSSystem();

// Show checkout modal
document.getElementById('checkout-btn').addEventListener('click', () => {
    document.getElementById('cart-modal').classList.add('hidden');
    document.getElementById('checkout-modal').classList.remove('hidden');
    document.getElementById('checkout-modal').classList.add('show');
});

// Add loading indicator to HTML
document.addEventListener('DOMContentLoaded', () => {
    const loading = document.createElement('div');
    loading.id = 'loading';
    loading.innerHTML = '<div class="spinner">⚡</div> Loading...';
    loading.style.cssText = 'position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:linear-gradient(145deg,#d84f8b,#b93873);color:#fff;padding:20px;border-radius:14px;box-shadow:0 16px 28px rgba(137,47,102,0.28);z-index:9999;display:none;';
    document.body.appendChild(loading);
});

// Expose POS for global access
window.pos = pos;
