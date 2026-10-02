# JARVIS Discord Shop Management Bot

JARVIS is a self-hosted Discord shop and server management bot backed by SQLite.

The included black `jarvis.jpg` artwork is used for personalized welcome cards and generated stock notifications. `jarvis-bot-banner.png` is a 960×540 version of that same artwork for a wide Discord banner. Keep the artwork beside `bot.py`; the bot needs Discord's **Attach Files** permission for image cards.

## Included features

- Admin, Staff, Seller, Customer, Member role setup; assigns Member when someone joins.
- Personalized welcome cards show the joining member's Discord avatar and display name. Configure a separate join role with `$autorole @role`; disable it with `$autorole off`.
- Security filter (enabled by default): removes Discord invite links, @everyone/@here or 6+ member mentions, duplicate messages repeated within 5 seconds, and bursts of 7 messages within 7 seconds. Incidents go to the `$setlog` channel. Admin can inspect/toggle it with `$security` and `$security on|off`.
- Persistent reaction-role mappings and ticket control buttons.
- Invite tracking with `$invites [@member]` and the Staff/Admin-only `$invitetop` leaderboard. JARVIS compares invite-use counts when members join and logs the inviter when a single invite can be identified.
- Purchase, Help, and Questions ticket panel with duplicate-open-ticket protection. Each button opens a regular ticket directly; Purchase no longer requires choosing a synced product first. Ticket openers can submit product, order ID, and issue details using the ticket form.
- Seller/Admin ticket claim, unclaim, close, transcript export, and log-channel reporting.
- SellAuth product catalog sync, shop embeds, stock updates and restock alerts.
- Gold JARVIS stock cards are generated with each new/changed product stock from `$syncproducts` and each manual `$restock`; item name, stock units, status, and `.gg/jarvismart` are rendered on the image.
- Storefront link for checkout. This project does **not** call the restricted SellAuth Checkout API or treat a ticket as proof of payment. Customer assignment follows an admin-verified order (`$ordercreate`) or manual verification (`$verifycustomer`).
- Order IDs, order history, customer profiles, loyalty points, vouches, seller claim stats, and staff dashboard.
- Welcome messages, giveaways, custom responses, moderation, maintenance flag, error/audit logs, and consistent daily SQLite backups.
- Guided `$setup` menus configure roles and channels inside Discord. `$checkup` reports missing bot permissions and configuration without exposing secrets.
- `$helpo` is a separate owner-only command index, grouped into setup, shop, server settings, moderation, and operations panels. `$help` stays member/staff role-filtered and `$helps` remains the detailed Staff/Admin guide.
- Safe restore flow: `$backuplist` lists backups; `$restorebackup <filename>` requires confirmation, validates the file, and creates a pre-restore safety copy.
- Automatic catalog monitoring: after SellAuth URL/key and an alert channel are set once, JARVIS syncs products every 6 hours and posts new or changed stock alerts.
- Prefix commands use no spaces in command names (for example `$addrole`, `$setwelcome`, `$syncproducts`).

## Setup

1. Install Python 3.10 or later.
2. Create a Discord application and bot, invite it with `bot` and `applications.commands` scopes, and grant the permissions listed below.
3. Enable **Server Members Intent**, **Message Content Intent**, and **Presence Intent** if desired in the Developer Portal. Message Content and Server Members are required by this bot.
4. Copy `.env.example` to `.env` and set `DISCORD_TOKEN`.
5. Install dependencies and start the bot:

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   py bot.py
   ```

### Deploy on Railway

The project includes `railway.json`, which sets the Railway start command to `python bot.py`; `requirements.txt` supplies the Python dependencies. Put the extracted project files at the root of the GitHub repository connected to Railway. In Railway, create a service from that repository and add `DISCORD_TOKEN` under **Variables** using the bot token from Discord Developer Portal (not the client secret or SellAuth token). Enable **Message Content Intent** and **Server Members Intent** in the Discord Developer Portal, then redeploy. This is a background bot worker, so it does not need a public domain or a `PORT` variable.

Railway's local filesystem is temporary between deployments unless you attach a Volume. To keep the SQLite database and backups, add a Railway Volume mounted at `/data` and set `DATA_DIR=/data` in the service Variables. If deployment still exits, open the deployment's **Logs** and share the first Python traceback or error lines; the final “process exited” line alone is not enough to diagnose it.

### Translation and store updates

Reply to any text message, mention Jarvis, and write `translate it` to translate it into English by default. For example, reply to `hello bhai kesa he`, mention Jarvis, and write `translate it`. Choose another target with `translate it hindi`, `translate it spanish`, or another language. Admins can use `$autotranslate on spanish` to translate new messages in the current channel, or `$autotranslate off` to disable it.

An Admin can send `$broadcast <text>` to DM the update to every current non-bot member who has the configured Verified role. Set that role with `$setverifyrole @Verified` first. There is no DM subscription panel; members who block server DMs may not receive the message, and the command reports delivery counts.

### Verified-only server access

Set the verification role and post the panel with `$setverifyrole @Verified` then `$verifypanel`. The panel grants the Verified role (and Member if configured) when clicked. To hide existing channels from unverified members, an Admin can run `$verificationlock`; Jarvis saves each channel's prior view permissions in SQLite. Run `$verificationunlock` to restore those saved view permissions. Discord users with the Administrator permission can always see channels. Keep a private Admin channel accessible before enabling the lock, and ensure Jarvis has Manage Channels permission and its role is high enough to edit channel permissions.

On first connection JARVIS creates the standard roles where it has permission. Role order and role assignment still obey Discord's bot-role hierarchy; place the JARVIS bot role above roles it needs to manage. The SQLite database, rotating daily backups (up to 15), and log file are created in `data/`.

## Discord permissions

Grant View Channels, Send Messages, Embed Links, Attach Files, Read Message History, Manage Channels, Manage Roles, Manage Messages, Add Reactions, Manage Server (to read invite usage), and moderate members (Timeout Members). Ban Members and Kick Members are only used by Admin commands. Keep the bot role above managed roles. Enable the intents listed in setup.

## First-time configuration

Run `$setup` to configure roles and channels with Discord menus, then run `$checkup` to see what is missing. Run `$help` for the role-filtered category menu, then use pages such as `$help ticket`, `$help shop`, `$help member`, `$help order`, `$help social`, `$help translation`, `$help verification`, or `$help staff`. Members see public commands; Staff/Admin see Staff tools; Sellers/Admin see ticket controls. `$helps` and `$helps <category>` show the Staff reference and reveal Admin pages only to Admins. Admin commands:

- `$config store_url https://your-storefront.example` — public purchase link.
- `$config sellauth_url https://api.sellauth.com` and `$config sellauth_key YOUR_KEY` — SellAuth catalog sync credentials. Configure these in a private admin-only channel. Use `$syncproducts` to fetch the product catalog endpoint `/v1/products`; if your SellAuth account/API plan uses a different catalog route or permissions, follow its current API documentation. JARVIS reports request errors and does not access checkout.
- `$setrole admin @role`, `$setrole staff @role`, `$setrole seller @role`, `$setrole customer @role`, `$setrole member @role` — use existing server roles if you want exact role IDs stored.
- `$fill` — Admin-only; create any missing standard roles (Admin, Staff, Seller, Customer, Member, Verified). JARVIS needs Manage Roles, and its highest role must be above the roles it creates.
- `$setlog #channel`, `$setwelcome #channel Welcome {user} to {server}!`, `$autorole @role` (or `$autorole off`), `$setticketcategory <category>`, `$setrestock #channel`, `$lowstock 3`. New or changed stock from `$syncproducts` and manual `$restock` posts a personalized stock card in the configured channel; if no stock channel is configured, it posts in the command channel.
- `$ticketpanel` posts the purchase/help/questions/product selector. `$shop` shows synced products.

## Role access

| Role | Access |
|---|---|
| Everyone / Member / Customer | Browse the shop, open tickets through the posted panel, view their own profile and orders, leave/view vouches, use translation, and join giveaways. The Verify button grants the configured Verified role. |
| Seller | Claim, unclaim, and close shop tickets; view their own seller statistics. Sellers do not have moderation or server-wide sales access. |
| Staff | Use timeout, warn, warning history, and purge; view the dashboard and sales report; look up other members' orders and seller statistics. |
| Admin | All Staff actions plus configuration, autorole, role changes, security settings, verification setup/lock, shop sync, broadcasts, backup, ban/kick, and other server-wide management commands. A member with Discord Administrator permission also counts as Admin. |

The bot also relies on Discord's own permissions and role hierarchy. Assign only trusted people to Staff/Admin, and keep the JARVIS bot role above roles it must grant or manage.

Most IDs and channel/role settings are stored in SQLite using `$config` and dedicated commands; the token is the only required environment value. Protect `.env` and avoid posting secrets in public channels. SellAuth credentials are stored in the local SQLite database; restrict access to the host and configure them in a private channel.

## Main commands

| Area | Commands |
|---|---|
| Help/shop | `$help` (role-aware), `$helps` (Staff/Admin), `$shop`, `$products`, `$store`, `$ticketpanel` |
| Profiles/orders | `$profile`, `$myorders`, `$order <id>`, `$ordercreate @user <product_id> <qty> <amount>`, `$verifycustomer @user`, `$vouch @seller <text>`, `$vouches` |
| Staff stats | `$dashboard`, `$sales`, `$sellerstats [@seller]` |
| Tickets | Help/Purchase/Questions panel buttons; `$ticketclaim`, `$ticketunclaim`, and `$ticketclose` plus matching Seller/Admin buttons |
| Admin settings | `$setup`, `$checkup`, `$config`, `$fill`, `$setrole`, `$setlog`, `$setwelcome`, `$autorole`, `$setticketcategory`, `$setrestock`, `$lowstock`, `$maintenance`, `$backup`, `$backuplist`, `$restorebackup <filename>` |
| Catalog | `$syncproducts`, `$restock <product_id> <stock>` |
| Roles | `$addrole @user @role`, `$removerole @user @role`, `$reactionrole set #channel MESSAGE_ID emoji @role`, `$reactionrole remove #channel MESSAGE_ID emoji @role` |
| Community | `$giveaway <minutes> <prize>`, `$giveawayend <message_id>`, `$customadd <word> <response>`, `$customremove <word>` (Admin-only custom auto-replies) |
| Invites | `$invites [@member]` (own count for members; Staff/Admin can look up others), `$invitetop` (Staff/Admin) |
| Moderation | `$timeout @user 10m <reason>`, `$to`, `$warn`, `$warnings`, `$purge <count>`, `$ban`, `$kick` |

`$ban` and `$kick` check the Admin role (or Discord Administrator permission). Staff and Seller can use timeout, warn, warnings, and purge. Ticket Claim/Unclaim/Close buttons are Seller/Admin only. Permission checks are enforced by the bot; Discord permissions must also be granted to the bot for each action.

## Orders and payment verification

SellAuth product sync is catalog-only. Payments remain on the configured storefront. Since this integration does not rely on restricted Checkout API access and no invoice webhook/user link is configured, JARVIS cannot independently prove an external purchase. Staff must verify it in SellAuth and an Admin records it with `$ordercreate`; this records an order ID, loyalty points, and assigns Customer if configured. `$verifycustomer` is a manual role-only fallback and does not create a sale record.

## Operations and limitations

- `$backup` sends a consistent SQLite snapshot to the invoking Admin; an automatic daily local backup is also kept. `$restorebackup` validates the selected snapshot and saves a separate safety copy before restoring.
- Automatic jobs run while the bot process is online: daily database backup, giveaway expiry checks, and SellAuth product/stock sync every 6 hours. Configure SellAuth URL/key and the restock channel once; no recurring manual sync is needed. JARVIS cannot safely guess your channel and role IDs, so use `$setup` once.
- Prefix commands need Discord's Message Content intent and persistent component views; keep the process running for scheduled tasks.
- Giveaway entry button confirms entry to the user, but winner selection currently reads reaction entrants on the giveaway message. (React with any emoji as well to qualify.)
- Low-stock threshold is configurable with `$lowstock`; low-stock alerts fire when a sync first detects a low value or when it changes to another low value. Manual `$restock` also emits restock and low-stock notifications. Numeric stock values are needed for the low-stock comparison.
- Invite attribution needs the bot's **Manage Server** permission. Only joins after tracking is active are counted; if multiple invite uses increase at once, an invite is deleted, or a vanity URL is used, JARVIS may log the join as unattributed. `$invitetop` shows only confidently attributed joins.
- SQLite is designed for a single bot process. Back up `data/` before moving hosts or upgrading.
- The bot does not include payment collection, refunds, inventory reservation, or automated fulfillment.

## Troubleshooting

- `DISCORD_TOKEN is missing`: set it in `.env`.
- No welcome/member assignment: enable Server Members Intent and check the bot role hierarchy.
- Missing command text: enable Message Content Intent and invite/grant permissions.
- Ticket/reaction role failures: grant Manage Channels/Manage Roles and move the bot role above the roles it assigns.
- SellAuth sync failure: verify your API key, product-read permissions, and the catalog route for your SellAuth account; checkout endpoints are not used.
- Runtime errors are written to `data/jarvis.log`.


