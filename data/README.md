# Business data

The agent works on four plain files, so they can be replaced with an export from any ERP, inventory or
CRM system (Odoo, Zoho, QuickBooks, HubSpot, Salesforce, Excel...). Falcon Office Supplies, its customers
and every email address here are fictional.

| File | One row per | Columns |
|---|---|---|
| `catalog.csv` | product | `sku`, `name`, `category`, `unit` (box, pack, each...), `pack_size` (items per unit), `item` (what one item is called), `price_aed`, `aliases` (other names customers use, separated by `;`) |
| `stock.csv` | product | `sku`, `dubai`, `abu_dhabi` (units on hand per warehouse), `restock_date`, `restock_qty` |
| `customers.csv` | customer | `id`, `company`, `contact`, `email`, `tier` (Standard / Silver / Gold), `payment_terms`, `city`, `credit_limit`, `open_balance` |
| `pricing.json` | - | VAT rate, quote validity, volume discounts, tier discounts, delivery fees per city, the order value that needs a manager |

`aliases` is where most of the matching quality comes from: add the words your customers really use
("post-its", "biros", "A4 reams") and the agent will find the product.

In production these files would be replaced by API calls to the real systems. Only the four tool
functions in `quoteflow/tools.py` and the loaders in `quoteflow/data.py` would change.
