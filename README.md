# Store Receipts

Home Assistant integration that shows the **latest receipt (kassabon)** of your Dutch supermarkets as a sensor.

| Store | Notes |
|---|---|
| Albert Heijn | App login (Appie account) |
| Jumbo | App login |
| Lidl | Lidl Plus account |
| Action | Mijn Action account |

> Unofficial. This project is not affiliated with Albert Heijn, Jumbo, Lidl or Action. The stores have no public receipt APIs, so this integration uses the same requests as their apps and websites. They can change without notice.

## What you get

One sensor per store you add:

- **State:** the date of the latest receipt.
- **Attributes:** the receipt details: items (name, quantity, price, total), the total, and the store/discount where the store provides them.

Receipts are refreshed every hour.

## Installation

**HACS (custom repository)**

1. HACS → ⋮ → *Custom repositories* → add `https://github.com/Xornop/ha-store-receipts` as type *Integration*.
2. Install **Store Receipts** and restart Home Assistant.

**Manual**

Copy `custom_components/store_receipts` to your `config/custom_components/` folder and restart Home Assistant.

## Setup

Go to **Settings → Devices & services → Add integration → Store Receipts**, pick a store, and paste a login token (see below). Repeat for each store.

You do not enter your store password in Home Assistant. You log in once in a browser and give Home Assistant a token instead.

### Use the Store Login app to get the Token

The **Store Login** app opens a browser inside Home Assistant, lets you log in to the store like normal, and gives you the token to paste. It handles the store-specific details (app redirects, cookies, Cloudflare) for you, and it is the easiest way to log in again later.

1. Add the app repository to Home Assistant (*Settings → apps → app store → ⋮ → Repositories*): `https://github.com/Xornop/ha-store-login`
2. Install and start **Store Login**, then open it from the sidebar.
3. Choose your store, log in in the browser window, and copy the token it shows.
4. In Home Assistant, add the **Store Receipts** integration, choose the same store and paste the token in the **Refresh token** field.

Use each token only once: some stores replace it on first use. If a store fails, delete the service for that store immediately, so your account doesn't get flagged as a bot.

## Logging in again

Sessions end eventually. When that happens Home Assistant shows a *Repair / Re-authenticate* notification: log in again with the app and paste the new token.
