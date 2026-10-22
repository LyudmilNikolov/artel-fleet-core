// FleetCore frontend utilities

// HTMX event handlers
document.addEventListener('htmx:afterSwap', function(event) {
    // Re-init Alpine components after HTMX swap
    if (window.Alpine) {
        Alpine.initTree(event.detail.target);
    }
});

// Toast notifications
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.innerHTML = `
        <div x-data="{ show: true }" x-show="show" x-init="setTimeout(() => { show = false; setTimeout(() => $el.parentElement.remove(), 300) }, 4000)"
             x-transition:leave="transition ease-in duration-200"
             x-transition:leave-start="opacity-100 translate-x-0"
             x-transition:leave-end="opacity-0 translate-x-full"
             class="flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg border
                    ${type === 'success' ? 'bg-green-50 border-green-200 text-green-800 dark:bg-green-900/30 dark:border-green-800 dark:text-green-400' :
                      type === 'error' ? 'bg-red-50 border-red-200 text-red-800 dark:bg-red-900/30 dark:border-red-800 dark:text-red-400' :
                      'bg-blue-50 border-blue-200 text-blue-800 dark:bg-blue-900/30 dark:border-blue-800 dark:text-blue-400'}">
            <span class="text-sm">${message}</span>
            <button @click="show = false; setTimeout(() => $el.closest('[x-data]').parentElement.remove(), 300)" class="ml-2 opacity-60 hover:opacity-100">&times;</button>
        </div>
    `;
    container.appendChild(toast);
    if (window.Alpine) {
        Alpine.initTree(toast);
    }
}

// Listen for HTMX response headers for toasts
document.addEventListener('htmx:afterRequest', function(event) {
    const xhr = event.detail.xhr;
    if (!xhr) return;
    const toast = xhr.getResponseHeader('HX-Trigger');
    if (toast) {
        try {
            const triggers = JSON.parse(toast);
            if (triggers.showToast) {
                showToast(triggers.showToast.message, triggers.showToast.type);
            }
        } catch (e) { /* not JSON */ }
    }
});

// Price per liter for the fuel expense form: amount / liters in whole cents, half up (the server's rule).
// Accepts a decimal comma. Returns '' until both numbers are usable.
window.fuelPricePerLiter = function(amount, liters) {
    var num = function(v) { return /^\d+([.,]\d+)?$/.test(String(v).trim()) ? Number(String(v).trim().replace(',', '.')) : NaN; };
    var cents = Math.round(num(amount) * 100), hundredths = Math.round(num(liters) * 100);
    if (!(cents >= 0) || !(hundredths > 0)) return '';
    var price = Math.floor((2 * cents * 100 + hundredths) / (2 * hundredths));
    return (price / 100).toFixed(2);
};

// Localized native validation: a field's data-msg-required / data-msg-format replace the browser-language text.
window.fcInvalid = function(el) {
    var v = el.validity;
    el.setCustomValidity(v.valueMissing ? (el.dataset.msgRequired || '') : v.patternMismatch ? (el.dataset.msgFormat || '') : '');
};

// Searchable vehicle picker shared by the maintenance and expense forms.
// Usage: x-data="vehiclePicker({vehicleId, label, tracked, currentMileage}, {extra: 'state'})"
document.addEventListener('alpine:init', function() {
    Alpine.data('vehiclePicker', function(cfg, extra) {
        return Object.assign({
            vehicleId: cfg.vehicleId || '',
            label: cfg.label || '',
            group: cfg.group || '',
            tracked: !!cfg.tracked,
            currentMileage: cfg.currentMileage === undefined ? null : cfg.currentMileage,
            docTypes: cfg.docTypes || [],
            blocked: cfg.blocked || [],
            serviceItems: cfg.serviceItems || [],
            allServiceItems: cfg.allServiceItems || [],
            open: false,
            choose: function(el) {
                this.vehicleId = el.dataset.id;
                this.label = el.dataset.label;
                this.group = el.dataset.group || '';
                this.tracked = el.dataset.tracked === '1';
                this.currentMileage = el.dataset.current === '' ? null : Number(el.dataset.current);
                this.docTypes = JSON.parse(el.dataset.docTypes || '[]');
                this.blocked = JSON.parse(el.dataset.blocked || '[]');
                this.serviceItems = JSON.parse(el.dataset.serviceItems || '[]');
                // a chosen item that does not apply to this vehicle (e.g. a trailer) is dropped
                if ('serviceItem' in this && !this.serviceItems.includes(this.serviceItem)) this.serviceItem = '';
                this.open = false;
            },
            clear: function() {
                this.vehicleId = '';
                this.label = '';
                this.group = '';
                this.tracked = false;
                this.currentMileage = null;
                this.docTypes = [];
                this.blocked = [];
                this.serviceItems = this.allServiceItems;
                this.$nextTick(() => this.$refs.search.focus());
            },
        }, extra || {});
    });
});


// --- CSRF: send the session token with every state-changing request -------------------------------------------
(function () {
    function token() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.getAttribute('content') : '';
    }
    // HTMX requests: header
    document.addEventListener('htmx:configRequest', function (event) {
        event.detail.headers['X-CSRF-Token'] = token();
    });
    // Plain forms: hidden field (added when the page loads, and again right before any submit)
    function addField(form) {
        var method = (form.getAttribute('method') || 'get').toLowerCase();
        if (method === 'get' || form.querySelector('input[name="csrf_token"]')) return;
        var input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'csrf_token';
        input.value = token();
        form.appendChild(input);
    }
    document.addEventListener('DOMContentLoaded', function () {
        document.querySelectorAll('form').forEach(addField);
    });
    document.addEventListener('htmx:afterSettle', function () {
        document.querySelectorAll('form').forEach(addField);
    });
    document.addEventListener('submit', function (event) {
        if (event.target && event.target.tagName === 'FORM') addField(event.target);
    }, true);
})();
