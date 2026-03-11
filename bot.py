"""
╔══════════════════════════════════════════════════════╗
║         STAKE ORIGINALS TELEGRAM BOT                 ║
║  Games: Dice · Limbo · Crash · Plinko · Wheel        ║
╚══════════════════════════════════════════════════════╝

Commands:
  /start          - Welcome message
  /settoken       - Link your Stake account
  /balance        - Check wallet balances
  /setdefault     - Save default bet amount & currency
  /deposit        - Get deposit address for a currency
  /withdraw       - Withdraw crypto to an external address
  /dice           - Play Dice
  /limbo          - Play Limbo
  /crash          - Play Crash
  /plinko         - Play Plinko
  /wheel          - Play Wheel
  /history        - Last 10 bets
  /help           - All commands
"""

import os, json, logging, httpx, uuid, asyncio
from typing import Optional
from telegram.helpers import escape_markdown

_file_lock = asyncio.Lock()
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, filters, ContextTypes,
)

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
STAKE_API_URL  = "https://api.stake.com/graphql"
DATA_FILE      = "users.json"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

CURRENCIES = ["btc", "eth", "ltc", "doge", "trx", "usdt", "bnb", "xrp"]

# ConversationHandler states
(
    ASK_AMOUNT, ASK_CURRENCY,
    DICE_CONDITION, DICE_TARGET,
    LIMBO_MULTI,
    CRASH_CASHOUT,
    PLINKO_ROWS, PLINKO_RISK,
    WHEEL_RISK, WHEEL_SEGMENTS,
    CONFIRM_BET,
    # Deposit
    DEPOSIT_CURRENCY,
    # Withdraw
    WITHDRAW_CURRENCY, WITHDRAW_AMOUNT, WITHDRAW_ADDRESS, WITHDRAW_2FA, CONFIRM_WITHDRAW,
) = range(17)

# ─────────────────────────────────────────────
#  PERSISTENCE
# ─────────────────────────────────────────────
def _load() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE) as f:
            return json.load(f)
    return {}

def _save(data: dict):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

async def get_user(user_id: int) -> dict:
    async with _file_lock:
        return _load().get(str(user_id), {})

async def save_user(user_id: int, udata: dict):
    async with _file_lock:
        data = _load()
        data[str(user_id)] = udata
        _save(data)

async def get_token(user_id: int) -> Optional[str]:
    return (await get_user(user_id)).get("token")

async def get_default(user_id: int) -> dict:
    u = await get_user(user_id)
    return {"amount": u.get("default_amount"), "currency": u.get("default_currency")}

# ─────────────────────────────────────────────
#  STAKE GRAPHQL CLIENT
# ─────────────────────────────────────────────
async def gql(token: str, query: str, variables: Optional[dict] = None) -> dict:
    headers = {"Content-Type": "application/json", "x-access-token": token}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(STAKE_API_URL, json={"query": query, "variables": variables or {}}, headers=headers)
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise ValueError(data["errors"][0]["message"])
        return data

# ─────────────────────────────────────────────
#  GRAPHQL DOCUMENTS
# ─────────────────────────────────────────────
GQL_BALANCE = """
query {
  user {
    id name
    balances { available { amount currency } }
  }
}"""

GQL_DICE = """
mutation DiceRoll($amount:Float!,$target:Float!,$condition:CasinoGameDiceConditionEnum!,$currency:CurrencyEnum!,$identifier:String!) {
  diceRoll(amount:$amount,target:$target,condition:$condition,currency:$currency,identifier:$identifier) {
    id amount payout payoutMultiplier currency createdAt
    state { result target condition }
  }
}"""

GQL_LIMBO = """
mutation LimboRoll($amount:Float!,$multiplierTarget:Float!,$currency:CurrencyEnum!,$identifier:String!) {
  limboRoll(amount:$amount,multiplierTarget:$multiplierTarget,currency:$currency,identifier:$identifier) {
    id amount payout payoutMultiplier currency createdAt
    state { result multiplierTarget }
  }
}"""

GQL_CRASH = """
mutation CrashBet($amount:Float!,$autoCashOut:Float!,$currency:CurrencyEnum!,$identifier:String!) {
  crashBet(amount:$amount,autoCashOut:$autoCashOut,currency:$currency,identifier:$identifier) {
    id amount payout payoutMultiplier currency createdAt
    state { result autoCashOut }
  }
}"""

GQL_PLINKO = """
mutation PlinkoRoll($amount:Float!,$rows:Int!,$risk:CasinoGamePlinkoRiskEnum!,$currency:CurrencyEnum!,$identifier:String!) {
  plinkoRoll(amount:$amount,rows:$rows,risk:$risk,currency:$currency,identifier:$identifier) {
    id amount payout payoutMultiplier currency createdAt
    state { result risk rows }
  }
}"""

GQL_WHEEL = """
mutation WheelSpin($amount:Float!,$segments:Int!,$risk:CasinoGameWheelRiskEnum!,$currency:CurrencyEnum!,$identifier:String!) {
  wheelSpin(amount:$amount,segments:$segments,risk:$risk,currency:$currency,identifier:$identifier) {
    id amount payout payoutMultiplier currency createdAt
    state { result segments risk }
  }
}"""

GQL_HISTORY = """
query($limit:Int!,$offset:Int!) {
  user {
    bets(limit:$limit,offset:$offset) {
      id amount payout currency createdAt
      game { name }
    }
  }
}"""

GQL_DEPOSIT_ADDRESS = """
query DepositAddress($currency: CurrencyEnum!) {
  user {
    wallet {
      depositAddresses(currency: $currency) {
        address
        currency
        network
      }
    }
  }
}"""

GQL_WITHDRAW = """
mutation CreateWithdrawal(
  $amount: Float!
  $currency: CurrencyEnum!
  $address: String!
  $twoFaToken: String
) {
  createWithdrawal(
    amount: $amount
    currency: $currency
    address: $address
    twoFaToken: $twoFaToken
  ) {
    id
    amount
    currency
    address
    status
    createdAt
  }
}"""

# ─────────────────────────────────────────────
#  RESULT FORMATTER
# ─────────────────────────────────────────────
def fmt_result(game: str, data: dict, amount: float, currency: str) -> str:
    payout = float(data.get("payout", 0))
    multi  = float(data.get("payoutMultiplier", 0))
    won    = payout > amount
    emoji  = "✅ WIN" if won else "❌ LOSS"
    profit = payout - amount

    lines = [f"{'─'*30}", f"🎮 *{game}*  {emoji}", f"{'─'*30}"]

    s = data.get("state", {})
    if game == "Dice":
        lines.append(f"🎲 Result:  `{float(s.get('result', 0)):.2f}`")
        lines.append(f"🎯 Target:  {s.get('condition','?')} `{float(s.get('target', 0)):.2f}`")
    elif game == "Limbo":
        lines.append(f"🚀 Result:  `{float(s.get('result',0)):.2f}x`")
        lines.append(f"🎯 Target:  ≥ `{float(s.get('multiplierTarget',0)):.2f}x`")
    elif game == "Crash":
        lines.append(f"💥 Crashed: `{float(s.get('result',0)):.2f}x`")
        lines.append(f"🎯 Cash-out: `{float(s.get('autoCashOut',0)):.2f}x`")
    elif game == "Plinko":
        lines.append(f"🔮 Multiplier: `{multi:.2f}x`")
        lines.append(f"📏 Rows: `{s.get('rows','?')}` | Risk: `{s.get('risk','?')}`")
    elif game == "Wheel":
        lines.append(f"🎡 Multiplier: `{float(s.get('result',0)):.2f}x`")
        lines.append(f"⚙️ Segments: `{s.get('segments','?')}` | Risk: `{s.get('risk','?')}`")

    lines += [
        f"{'─'*30}",
        f"💰 Bet:    `{amount:.8f}` {currency.upper()}",
        f"💵 Payout: `{payout:.8f}` {currency.upper()}",
        f"{'📈' if won else '📉'} Profit:  `{profit:+.8f}` {currency.upper()}",
        f"⚡ Multi:  `{multi:.4f}x`",
    ]
    return "\n".join(lines)

# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────
def build_currency_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(c.upper(), callback_data=f"cur_{c}") for c in CURRENCIES[:4]],
        [InlineKeyboardButton(c.upper(), callback_data=f"cur_{c}") for c in CURRENCIES[4:]],
    ]
    return InlineKeyboardMarkup(rows)

def uid(update: Update) -> int:
    return update.effective_user.id

async def no_token(update: Update):
    text = "❌ No Stake token linked.\nUse /settoken YOUR_TOKEN first."
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text)

# ─────────────────────────────────────────────
#  /start  /help
# ─────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎰 *Stake Originals Bot*\n\n"
        "Bet on Stake.com Originals right from Telegram!\n\n"
        "*Supported games:*\n"
        "🎲 Dice  🚀 Limbo  💥 Crash  🔮 Plinko  🎡 Wheel\n\n"
        "*Wallet:*\n"
        "📥 /deposit — Get deposit address\n"
        "📤 /withdraw — Withdraw to external wallet\n"
        "💼 /balance — Check balances\n\n"
        "*Quick start:*\n"
        "1️⃣ `/settoken YOUR_TOKEN` — link your account\n"
        "2️⃣ `/setdefault` — save a default bet amount\n"
        "3️⃣ Pick a game: /dice /limbo /crash /plinko /wheel\n\n"
        "Type /help for all commands.",
        parse_mode="Markdown",
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *Commands*\n\n"
        "/settoken `<token>` — Link Stake account\n"
        "/balance — Check wallet balances\n"
        "/setdefault — Save default bet amount & currency\n"
        "/deposit — 📥 Get deposit address\n"
        "/withdraw — 📤 Withdraw to external wallet\n"
        "/history — Last 10 bets\n\n"
        "🎮 *Games*\n"
        "/dice — 🎲 Dice (over/under)\n"
        "/limbo — 🚀 Limbo (multiplier target)\n"
        "/crash — 💥 Crash (auto cash-out)\n"
        "/plinko — 🔮 Plinko (rows & risk)\n"
        "/wheel — 🎡 Wheel (segments & risk)\n\n"
        "Each game wizard lets you use your saved default or enter a custom amount.\n\n"
        "Type /cancel at any time to abort the current action.",
        parse_mode="Markdown",
    )

# ─────────────────────────────────────────────
#  /settoken
# ─────────────────────────────────────────────
async def cmd_settoken(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("⚠️ Usage: `/settoken YOUR_TOKEN`", parse_mode="Markdown")
        return
    token = ctx.args[0].strip()
    msg   = await update.message.reply_text("🔄 Verifying token…")
    try:
        res  = await gql(token, GQL_BALANCE)
        name = res["data"]["user"]["name"]
        u    = await get_user(uid(update))
        u["token"] = token
        await save_user(uid(update), u)
        await msg.edit_text(f"✅ Linked! Welcome, *{escape_markdown(name)}*.", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Token invalid or expired.\n`{e}`", parse_mode="Markdown")

# ─────────────────────────────────────────────
#  /balance
# ─────────────────────────────────────────────
async def cmd_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    token = await get_token(uid(update))
    if not token:
        await no_token(update); return
    msg = await update.message.reply_text("🔄 Fetching balances…")
    try:
        res  = await gql(token, GQL_BALANCE)
        user = res["data"]["user"]
        bals = [b for b in user["balances"]["available"] if float(b["amount"]) > 0]
        lines = [f"💼 *{escape_markdown(user['name'])}'s Balances*\n"]
        if bals:
            for b in bals:
                lines.append(f"  • *{b['currency'].upper()}*: `{float(b['amount']):.8f}`")
        else:
            lines.append("_All balances are zero._")
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")

# ─────────────────────────────────────────────
#  /history
# ─────────────────────────────────────────────
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    token = await get_token(uid(update))
    if not token:
        await no_token(update); return
    msg = await update.message.reply_text("🔄 Fetching history…")
    try:
        res  = await gql(token, GQL_HISTORY, {"limit": 10, "offset": 0})
        bets = res["data"]["user"]["bets"]
        if not bets:
            await msg.edit_text("📭 No bets found."); return
        lines = ["📜 *Last 10 Bets*\n"]
        for b in bets:
            won    = float(b["payout"]) > float(b["amount"])
            emoji  = "✅" if won else "❌"
            game   = (b.get("game") or {}).get("name") or "Unknown"
            lines.append(
                f"{emoji} *{escape_markdown(game)}* | `{float(b['amount']):.6f}` → `{float(b['payout']):.6f}` "
                f"{b['currency'].upper()} | {b['createdAt'][:10]}"
            )
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")

# ─────────────────────────────────────────────
#  /setdefault  (ConversationHandler)
# ─────────────────────────────────────────────
async def cmd_setdefault(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "💾 *Set Default Bet*\n\nEnter your default bet amount (e.g. `0.001`):",
        parse_mode="Markdown",
    )
    return ASK_AMOUNT

async def setdefault_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        val = float(update.message.text.strip())
        if val <= 0:
            raise ValueError
        ctx.user_data["def_amount"] = val
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Must be greater than 0. Try again:"); return ASK_AMOUNT
    await update.message.reply_text("Choose currency:", reply_markup=build_currency_keyboard())
    return ASK_CURRENCY

async def setdefault_currency(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query    = update.callback_query
    await query.answer()
    currency = query.data.replace("cur_", "")
    amount   = ctx.user_data["def_amount"]
    u        = await get_user(uid(update))
    u["default_amount"]   = amount
    u["default_currency"] = currency
    await save_user(uid(update), u)
    await query.edit_message_text(
        f"✅ Default set: `{amount}` *{currency.upper()}*", parse_mode="Markdown"
    )
    return ConversationHandler.END

# ─────────────────────────────────────────────
#  SHARED BET AMOUNT STEP (used by all games)
# ─────────────────────────────────────────────
async def amount_keyboard(user_id: int) -> InlineKeyboardMarkup:
    d = await get_default(user_id)
    rows = []
    if d["amount"] and d["currency"]:
        rows.append([InlineKeyboardButton(
            f"Use default: {d['amount']} {d['currency'].upper()}",
            callback_data="use_default"
        )])
    rows.append([InlineKeyboardButton("Enter custom amount", callback_data="custom_amount")])
    return InlineKeyboardMarkup(rows)

# ─────────────────────────────────────────────
#  DICE GAME
# ─────────────────────────────────────────────
async def cmd_dice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await get_token(uid(update)):
        await no_token(update); return ConversationHandler.END
    ctx.user_data["game"] = "dice"
    await update.message.reply_text(
        "🎲 *Dice Bet*\n\nHow much do you want to bet?",
        parse_mode="Markdown",
        reply_markup=await amount_keyboard(uid(update)),
    )
    return ASK_AMOUNT

async def dice_condition(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⬆️ Above", callback_data="cond_above"),
        InlineKeyboardButton("⬇️ Below", callback_data="cond_below"),
    ]])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🎲 Choose condition:", reply_markup=kb)
    else:
        await update.message.reply_text("🎲 Choose condition:", reply_markup=kb)
    return DICE_CONDITION

async def dice_target(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["dice_condition"] = query.data.replace("cond_", "")
    cond = ctx.user_data["dice_condition"]
    hint = "e.g. `75` (win if result > 75)" if cond == "above" else "e.g. `25` (win if result < 25)"
    await query.edit_message_text(
        f"🎯 Enter target number (0–100):\n_{hint}_", parse_mode="Markdown"
    )
    return DICE_TARGET

async def dice_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        target = float(update.message.text.strip())
        if not 0 < target < 100:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a number between 0 and 100:"); return DICE_TARGET
    ctx.user_data["dice_target"] = target
    amt = ctx.user_data["bet_amount"]
    cur = ctx.user_data["bet_currency"]
    cond = ctx.user_data["dice_condition"]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Cancel",  callback_data="confirm_no"),
    ]])
    await update.message.reply_text(
        f"🎲 *Dice — Confirm Bet*\n\n"
        f"Amount: `{amt}` {cur.upper()}\n"
        f"Condition: *{cond}* `{target:.2f}`",
        parse_mode="Markdown", reply_markup=kb,
    )
    return CONFIRM_BET

async def execute_dice(token, d):
    return await gql(token, GQL_DICE, {
        "amount":     d["bet_amount"],
        "target":     d["dice_target"],
        "condition":  d["dice_condition"],
        "currency":   d["bet_currency"],
        "identifier": str(uuid.uuid4()),
    })

# ─────────────────────────────────────────────
#  LIMBO GAME
# ─────────────────────────────────────────────
async def cmd_limbo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await get_token(uid(update)):
        await no_token(update); return ConversationHandler.END
    ctx.user_data["game"] = "limbo"
    await update.message.reply_text(
        "🚀 *Limbo Bet*\n\nHow much do you want to bet?",
        parse_mode="Markdown",
        reply_markup=await amount_keyboard(uid(update)),
    )
    return ASK_AMOUNT

async def limbo_multiplier(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "🚀 Enter multiplier target (e.g. `2.0`):", parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("🚀 Enter multiplier target (e.g. `2.0`):", parse_mode="Markdown")
    return LIMBO_MULTI

async def limbo_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        multi = float(update.message.text.strip())
        if multi < 1.01:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a number ≥ 1.01:"); return LIMBO_MULTI
    ctx.user_data["limbo_multi"] = multi
    amt = ctx.user_data["bet_amount"]
    cur = ctx.user_data["bet_currency"]
    kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Cancel",  callback_data="confirm_no"),
    ]])
    await update.message.reply_text(
        f"🚀 *Limbo — Confirm Bet*\n\n"
        f"Amount: `{amt}` {cur.upper()}\n"
        f"Multiplier target: `{multi:.2f}x`",
        parse_mode="Markdown", reply_markup=kb,
    )
    return CONFIRM_BET

async def execute_limbo(token, d):
    return await gql(token, GQL_LIMBO, {
        "amount":           d["bet_amount"],
        "multiplierTarget": d["limbo_multi"],
        "currency":         d["bet_currency"],
        "identifier":       str(uuid.uuid4()),
    })

# ─────────────────────────────────────────────
#  CRASH GAME
# ─────────────────────────────────────────────
async def cmd_crash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await get_token(uid(update)):
        await no_token(update); return ConversationHandler.END
    ctx.user_data["game"] = "crash"
    await update.message.reply_text(
        "💥 *Crash Bet*\n\nHow much do you want to bet?",
        parse_mode="Markdown",
        reply_markup=await amount_keyboard(uid(update)),
    )
    return ASK_AMOUNT

async def crash_cashout(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "💥 Enter auto cash-out multiplier (e.g. `2.0`):", parse_mode="Markdown"
        )
    else:
        await update.message.reply_text("💥 Enter auto cash-out multiplier (e.g. `2.0`):", parse_mode="Markdown")
    return CRASH_CASHOUT

async def crash_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        cashout = float(update.message.text.strip())
        if cashout < 1.01:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a number ≥ 1.01:"); return CRASH_CASHOUT
    ctx.user_data["crash_cashout"] = cashout
    amt = ctx.user_data["bet_amount"]
    cur = ctx.user_data["bet_currency"]
    kb  = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Cancel",  callback_data="confirm_no"),
    ]])
    await update.message.reply_text(
        f"💥 *Crash — Confirm Bet*\n\n"
        f"Amount: `{amt}` {cur.upper()}\n"
        f"Auto cash-out: `{cashout:.2f}x`",
        parse_mode="Markdown", reply_markup=kb,
    )
    return CONFIRM_BET

async def execute_crash(token, d):
    return await gql(token, GQL_CRASH, {
        "amount":      d["bet_amount"],
        "autoCashOut": d["crash_cashout"],
        "currency":    d["bet_currency"],
        "identifier":  str(uuid.uuid4()),
    })

# ─────────────────────────────────────────────
#  PLINKO GAME
# ─────────────────────────────────────────────
async def cmd_plinko(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await get_token(uid(update)):
        await no_token(update); return ConversationHandler.END
    ctx.user_data["game"] = "plinko"
    await update.message.reply_text(
        "🔮 *Plinko Bet*\n\nHow much do you want to bet?",
        parse_mode="Markdown",
        reply_markup=await amount_keyboard(uid(update)),
    )
    return ASK_AMOUNT

async def plinko_rows(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(str(r), callback_data=f"rows_{r}") for r in [8, 10, 12]
    ], [
        InlineKeyboardButton(str(r), callback_data=f"rows_{r}") for r in [14, 16]
    ]])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🔮 Choose number of rows:", reply_markup=kb)
    else:
        await update.message.reply_text("🔮 Choose number of rows:", reply_markup=kb)
    return PLINKO_ROWS

async def plinko_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["plinko_rows"] = int(query.data.replace("rows_", ""))
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Low",    callback_data="risk_low"),
        InlineKeyboardButton("🟡 Medium", callback_data="risk_medium"),
        InlineKeyboardButton("🔴 High",   callback_data="risk_high"),
    ]])
    await query.edit_message_text("🔮 Choose risk level:", reply_markup=kb)
    return PLINKO_RISK

async def plinko_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["plinko_risk"] = query.data.replace("risk_", "")
    amt  = ctx.user_data["bet_amount"]
    cur  = ctx.user_data["bet_currency"]
    rows = ctx.user_data["plinko_rows"]
    risk = ctx.user_data["plinko_risk"]
    kb   = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Cancel",  callback_data="confirm_no"),
    ]])
    await query.edit_message_text(
        f"🔮 *Plinko — Confirm Bet*\n\n"
        f"Amount: `{amt}` {cur.upper()}\n"
        f"Rows: `{rows}` | Risk: `{risk}`",
        parse_mode="Markdown", reply_markup=kb,
    )
    return CONFIRM_BET

async def execute_plinko(token, d):
    return await gql(token, GQL_PLINKO, {
        "amount":     d["bet_amount"],
        "rows":       d["plinko_rows"],
        "risk":       d["plinko_risk"],
        "currency":   d["bet_currency"],
        "identifier": str(uuid.uuid4()),
    })

# ─────────────────────────────────────────────
#  WHEEL GAME
# ─────────────────────────────────────────────
async def cmd_wheel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await get_token(uid(update)):
        await no_token(update); return ConversationHandler.END
    ctx.user_data["game"] = "wheel"
    await update.message.reply_text(
        "🎡 *Wheel Bet*\n\nHow much do you want to bet?",
        parse_mode="Markdown",
        reply_markup=await amount_keyboard(uid(update)),
    )
    return ASK_AMOUNT

async def wheel_risk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🟢 Low",    callback_data="wrisk_low"),
        InlineKeyboardButton("🟡 Medium", callback_data="wrisk_medium"),
        InlineKeyboardButton("🔴 High",   callback_data="wrisk_high"),
    ]])
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("🎡 Choose risk level:", reply_markup=kb)
    else:
        await update.message.reply_text("🎡 Choose risk level:", reply_markup=kb)
    return WHEEL_RISK

async def wheel_segments(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["wheel_risk"] = query.data.replace("wrisk_", "")
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(str(s), callback_data=f"seg_{s}") for s in [10, 20, 30, 40, 50]
    ]])
    await query.edit_message_text("🎡 Choose number of segments:", reply_markup=kb)
    return WHEEL_SEGMENTS

async def wheel_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["wheel_segments"] = int(query.data.replace("seg_", ""))
    amt  = ctx.user_data["bet_amount"]
    cur  = ctx.user_data["bet_currency"]
    risk = ctx.user_data["wheel_risk"]
    segs = ctx.user_data["wheel_segments"]
    kb   = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm", callback_data="confirm_yes"),
        InlineKeyboardButton("❌ Cancel",  callback_data="confirm_no"),
    ]])
    await query.edit_message_text(
        f"🎡 *Wheel — Confirm Bet*\n\n"
        f"Amount: `{amt}` {cur.upper()}\n"
        f"Risk: `{risk}` | Segments: `{segs}`",
        parse_mode="Markdown", reply_markup=kb,
    )
    return CONFIRM_BET

async def execute_wheel(token, d):
    return await gql(token, GQL_WHEEL, {
        "amount":     d["bet_amount"],
        "segments":   d["wheel_segments"],
        "risk":       d["wheel_risk"],
        "currency":   d["bet_currency"],
        "identifier": str(uuid.uuid4()),
    })

# ─────────────────────────────────────────────
#  SHARED: AMOUNT FLOW & CONFIRM
# ─────────────────────────────────────────────
GAME_NEXT = {
    "dice":   dice_condition,
    "limbo":  limbo_multiplier,
    "crash":  crash_cashout,
    "plinko": plinko_rows,
    "wheel":  wheel_risk,
}

async def handle_amount_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "use_default":
        d = await get_default(uid(update))
        ctx.user_data["bet_amount"]   = d["amount"]
        ctx.user_data["bet_currency"] = d["currency"]
        return await GAME_NEXT[ctx.user_data["game"]](update, ctx)
    else:  # custom_amount
        await query.edit_message_text("💬 Enter custom bet amount (e.g. `0.001`):", parse_mode="Markdown")
        return ASK_AMOUNT

async def handle_custom_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Enter a positive number:"); return ASK_AMOUNT
    ctx.user_data["bet_amount"] = amount
    await update.message.reply_text("💱 Choose currency:", reply_markup=build_currency_keyboard())
    return ASK_CURRENCY

async def handle_currency_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["bet_currency"] = query.data.replace("cur_", "")
    return await GAME_NEXT[ctx.user_data["game"]](update, ctx)

GAME_EXECUTE = {
    "dice":   (execute_dice,   "Dice"),
    "limbo":  (execute_limbo,  "Limbo"),
    "crash":  (execute_crash,  "Crash"),
    "plinko": (execute_plinko, "Plinko"),
    "wheel":  (execute_wheel,  "Wheel"),
}

async def handle_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_no":
        await query.edit_message_text("❌ Bet cancelled.")
        return ConversationHandler.END

    await query.edit_message_text("🔄 Placing bet…")
    token  = await get_token(uid(update))
    game   = ctx.user_data["game"]
    ex_fn, game_name = GAME_EXECUTE[game]
    try:
        res  = await ex_fn(token, ctx.user_data)
        data_map = res.get("data") or {}
        if not data_map:
            raise ValueError("Empty response from Stake API")
        key  = list(data_map.keys())[0]
        data = data_map[key]
        if not data:
            raise ValueError("Bet response was null — check your balance or settings")
        text = fmt_result(
            game_name, data,
            ctx.user_data["bet_amount"],
            ctx.user_data["bet_currency"],
        )
        await query.edit_message_text(text, parse_mode="Markdown")
    except Exception as e:
        await query.edit_message_text(f"❌ Bet failed:\n`{e}`", parse_mode="Markdown")
    return ConversationHandler.END

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text("❌ Cancelled.")
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Cancelled.")
    return ConversationHandler.END

# ─────────────────────────────────────────────
#  DEPOSIT
# ─────────────────────────────────────────────

async def cmd_deposit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    token = await get_token(uid(update))
    if not token:
        await no_token(update); return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(c.upper(), callback_data=f"dep_{c}") for c in CURRENCIES[:4]],
        [InlineKeyboardButton(c.upper(), callback_data=f"dep_{c}") for c in CURRENCIES[4:]],
    ])
    await update.message.reply_text(
        "📥 *Deposit*\n\nSelect which currency you want to deposit:",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return DEPOSIT_CURRENCY

async def deposit_show_address(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    currency = query.data.replace("dep_", "")
    token    = await get_token(uid(update))

    await query.edit_message_text(f"🔄 Fetching {currency.upper()} deposit address…")
    try:
        res  = await gql(token, GQL_DEPOSIT_ADDRESS, {"currency": currency})
        addresses = (
            res.get("data", {})
               .get("user", {})
               .get("wallet", {})
               .get("depositAddresses", [])
        )
        if not addresses:
            await query.edit_message_text(
                f"❌ No deposit address found for *{currency.upper()}*.\n"
                "This currency may not be supported on your account.",
                parse_mode="Markdown",
            )
            return ConversationHandler.END

        lines = [f"📥 *{currency.upper()} Deposit Address*\n"]
        for entry in addresses:
            network = entry.get("network") or currency.upper()
            address = entry.get("address", "N/A")
            lines.append(f"🌐 Network: `{network.upper()}`")
            lines.append(f"📋 Address:\n`{address}`\n")
        lines.append("⚠️ _Only send {cur} to this address. Sending other assets may result in permanent loss._".format(cur=currency.upper()))
        await query.edit_message_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        await query.edit_message_text(f"❌ Error fetching deposit address:\n`{e}`", parse_mode="Markdown")
    return ConversationHandler.END

deposit_conv = ConversationHandler(
    entry_points=[CommandHandler("deposit", cmd_deposit)],
    states={
        DEPOSIT_CURRENCY: [CallbackQueryHandler(deposit_show_address, pattern="^dep_")],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

# ─────────────────────────────────────────────
#  WITHDRAW
# ─────────────────────────────────────────────

async def cmd_withdraw(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    token = await get_token(uid(update))
    if not token:
        await no_token(update); return ConversationHandler.END
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(c.upper(), callback_data=f"wd_{c}") for c in CURRENCIES[:4]],
        [InlineKeyboardButton(c.upper(), callback_data=f"wd_{c}") for c in CURRENCIES[4:]],
    ])
    await update.message.reply_text(
        "📤 *Withdraw*\n\nSelect the currency to withdraw:",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return WITHDRAW_CURRENCY

async def withdraw_got_currency(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["wd_currency"] = query.data.replace("wd_", "")
    cur = ctx.user_data["wd_currency"].upper()
    await query.edit_message_text(
        f"📤 *Withdraw {cur}*\n\nEnter the amount to withdraw (e.g. `0.005`):",
        parse_mode="Markdown",
    )
    return WITHDRAW_AMOUNT

async def withdraw_got_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        amount = float(update.message.text.strip())
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Must be a positive number:"); return WITHDRAW_AMOUNT
    ctx.user_data["wd_amount"] = amount
    cur = ctx.user_data["wd_currency"].upper()
    await update.message.reply_text(
        f"📤 *Withdraw {cur}*\n\nEnter the destination wallet address:",
        parse_mode="Markdown",
    )
    return WITHDRAW_ADDRESS

async def withdraw_got_address(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    address = update.message.text.strip()
    # Bug fix: reject addresses with internal spaces (copy-paste errors)
    if len(address) < 10 or ' ' in address:
        await update.message.reply_text(
            "❌ Invalid address — too short or contains spaces. Please re-enter:"
        )
        return WITHDRAW_ADDRESS
    ctx.user_data["wd_address"] = address

    cur    = ctx.user_data["wd_currency"].upper()
    amount = ctx.user_data["wd_amount"]
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Confirm Withdrawal", callback_data="wd_confirm_yes"),
        InlineKeyboardButton("❌ Cancel",             callback_data="wd_confirm_no"),
    ]])
    await update.message.reply_text(
        f"📤 *Confirm Withdrawal*\n\n"
        f"{'─'*28}\n"
        f"💰 Amount:  `{amount:.8f}` {cur}\n"
        f"📋 Address: `{escape_markdown(address)}`\n"
        f"{'─'*28}\n"
        f"⚠️ _Double-check the address — this cannot be undone._",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return CONFIRM_WITHDRAW

async def withdraw_got_2fa(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Called AFTER confirmation — 2FA is fresh and won't expire before execution."""
    raw = update.message.text.strip()
    ctx.user_data["wd_2fa"] = None if raw.lower() == "skip" else raw
    # Immediately execute — no extra confirmation step so TOTP doesn't expire
    await _do_withdraw(update.message, ctx)
    return ConversationHandler.END

async def withdraw_execute(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User clicked Confirm/Cancel on the withdrawal preview."""
    query = update.callback_query
    await query.answer()
    if query.data == "wd_confirm_no":
        await query.edit_message_text("❌ Withdrawal cancelled.")
        return ConversationHandler.END
    # Ask for 2FA NOW — right before execution so TOTP is always fresh
    await query.edit_message_text(
        "🔐 *Two-Factor Authentication*\n\n"
        "Enter your 2FA code if enabled on your account,\nor type `skip` to proceed without it:",
        parse_mode="Markdown",
    )
    return WITHDRAW_2FA

async def _do_withdraw(msg_obj, ctx: ContextTypes.DEFAULT_TYPE):
    """Execute the withdrawal immediately after 2FA is collected."""
    status_msg = await msg_obj.reply_text("🔄 Processing withdrawal…")
    token   = await get_token(msg_obj.from_user.id)
    cur     = ctx.user_data["wd_currency"]
    amount  = ctx.user_data["wd_amount"]
    address = ctx.user_data["wd_address"]
    two_fa  = ctx.user_data.get("wd_2fa")
    try:
        variables: dict = {"amount": amount, "currency": cur, "address": address}
        if two_fa:
            variables["twoFaToken"] = two_fa
        res = await gql(token, GQL_WITHDRAW, variables)
        wd  = (res.get("data") or {}).get("createWithdrawal")
        if not wd:
            raise ValueError("Withdrawal rejected — check balance, address, or 2FA code")
        # Bug fix 4: safe float via .get()
        amt_out  = float(wd.get("amount") or 0)
        # Bug fix 7: escape address for Markdown
        addr_out = escape_markdown(str(wd.get("address") or address))
        status   = (wd.get("status") or "pending").upper()
        wid      = wd.get("id", "N/A")
        ts       = (wd.get("createdAt") or "")[:10]
        await status_msg.edit_text(
            f"✅ *Withdrawal Submitted*\n\n"
            f"{'─'*28}\n"
            f"🆔 ID:      `{wid}`\n"
            f"💰 Amount:  `{amt_out:.8f}` {cur.upper()}\n"
            f"📋 Address: `{addr_out}`\n"
            f"📊 Status:  `{status}`\n"
            f"📅 Date:    `{ts}`\n"
            f"{'─'*28}\n"
            f"_Funds will arrive after network confirmation._",
            parse_mode="Markdown",
        )
    except Exception as e:
        await status_msg.edit_text(
            f"❌ Withdrawal failed:\n`{e}`\n\n"
            "_Check your balance, address, and 2FA code then try again._",
            parse_mode="Markdown",
        )

withdraw_conv = ConversationHandler(
    entry_points=[CommandHandler("withdraw", cmd_withdraw)],
    states={
        WITHDRAW_CURRENCY: [CallbackQueryHandler(withdraw_got_currency, pattern="^wd_[a-z]+$")],
        WITHDRAW_AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_got_amount)],
        WITHDRAW_ADDRESS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_got_address)],
        CONFIRM_WITHDRAW:  [CallbackQueryHandler(withdraw_execute, pattern="^wd_confirm_")],
        WITHDRAW_2FA:      [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_got_2fa)],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

# ─────────────────────────────────────────────
#  /setdefault conversation
# ─────────────────────────────────────────────
setdefault_conv = ConversationHandler(
    entry_points=[CommandHandler("setdefault", cmd_setdefault)],
    states={
        ASK_AMOUNT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, setdefault_amount)],
        ASK_CURRENCY: [CallbackQueryHandler(setdefault_currency, pattern="^cur_")],
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

# ─────────────────────────────────────────────
#  GAME CONVERSATION HANDLER
# ─────────────────────────────────────────────
def make_game_conv(entry_cmd, entry_fn) -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler(entry_cmd, entry_fn)],
        states={
            ASK_AMOUNT: [
                CallbackQueryHandler(handle_amount_choice, pattern="^(use_default|custom_amount)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_amount),
            ],
            ASK_CURRENCY: [
                CallbackQueryHandler(handle_currency_choice, pattern="^cur_"),
            ],
            DICE_CONDITION: [
                CallbackQueryHandler(dice_target, pattern="^cond_"),
            ],
            DICE_TARGET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, dice_confirm),
            ],
            LIMBO_MULTI: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, limbo_confirm),
            ],
            CRASH_CASHOUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, crash_confirm),
            ],
            PLINKO_ROWS: [
                CallbackQueryHandler(plinko_risk, pattern="^rows_"),
            ],
            PLINKO_RISK: [
                CallbackQueryHandler(plinko_confirm, pattern="^risk_"),
            ],
            WHEEL_RISK: [
                CallbackQueryHandler(wheel_segments, pattern="^wrisk_"),
            ],
            WHEEL_SEGMENTS: [
                CallbackQueryHandler(wheel_confirm, pattern="^seg_"),
            ],
            CONFIRM_BET: [
                CallbackQueryHandler(handle_confirm, pattern="^confirm_"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Simple commands
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("settoken",cmd_settoken))
    app.add_handler(CommandHandler("balance", cmd_balance))
    app.add_handler(CommandHandler("history", cmd_history))

    # /setdefault conversation
    app.add_handler(setdefault_conv)

    # Deposit & Withdraw conversations
    app.add_handler(deposit_conv)
    app.add_handler(withdraw_conv)

    # Game conversations
    app.add_handler(make_game_conv("dice",   cmd_dice))
    app.add_handler(make_game_conv("limbo",  cmd_limbo))
    app.add_handler(make_game_conv("crash",  cmd_crash))
    app.add_handler(make_game_conv("plinko", cmd_plinko))
    app.add_handler(make_game_conv("wheel",  cmd_wheel))

    logger.info("🎰 Stake Originals Bot is running...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
