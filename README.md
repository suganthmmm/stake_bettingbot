# 🎰 Stake Originals Telegram Bot

Bet on Stake.com Original games directly from Telegram.

**Supported games:** Dice 🎲 · Limbo 🚀 · Crash 💥 · Plinko 🔮 · Wheel 🎡

---

## ⚙️ Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Create your Telegram Bot
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot` and follow the steps
3. Copy the **Bot Token**

### 3. Set your token
Edit `bot.py` line 40 — replace `YOUR_TELEGRAM_BOT_TOKEN`:
```python
TELEGRAM_TOKEN = "123456789:ABCdef..."
```
Or use an environment variable (recommended):
```bash
export TELEGRAM_TOKEN="your_token_here"
```

### 4. Run the bot
```bash
python bot.py
```

---

## 🔑 Linking your Stake Account

To get your Stake `x-access-token`:

1. Log in to [stake.com](https://stake.com) in Chrome/Firefox
2. Open **DevTools** → `F12`
3. Go to the **Network** tab
4. Click anything on Stake (e.g. play a game)
5. Find a request to `api.stake.com/graphql`
6. In the **Headers** section, copy the value of `x-access-token`
7. Send `/settoken YOUR_TOKEN` in the bot

---

## 📋 Commands

| Command | Description |
|---|---|
| `/start` | Welcome & guide |
| `/settoken <token>` | Link your Stake account |
| `/balance` | Check all wallet balances |
| `/setdefault` | Save default bet amount & currency |
| `/dice` | 🎲 Play Dice |
| `/limbo` | 🚀 Play Limbo |
| `/crash` | 💥 Play Crash |
| `/plinko` | 🔮 Play Plinko |
| `/wheel` | 🎡 Play Wheel |
| `/history` | Last 10 bets |
| `/help` | All commands |
| `/cancel` | Cancel current action |

---

## 🎮 How Each Game Works

| Game | Your Input | Win Condition |
|---|---|---|
| **Dice** | Target number + Over/Under | Roll lands on your side |
| **Limbo** | Multiplier target (e.g. 2.0x) | Result ≥ target |
| **Crash** | Auto cash-out (e.g. 2.0x) | Rocket doesn't crash before cash-out |
| **Plinko** | Rows (8–16) + Risk level | Ball lands on high multiplier slot |
| **Wheel** | Segments (10–50) + Risk level | Wheel lands on multiplier slot |

---

## ⚠️ Disclaimer

This bot uses the **unofficial** Stake.com GraphQL API.
Use responsibly. Gambling involves risk of financial loss.
