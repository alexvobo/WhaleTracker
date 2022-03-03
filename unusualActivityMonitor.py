import asyncio
import datetime
import json
import hmac
import hashlib
import time
import base64
import asyncio
import time
from time import strftime
import websockets
import unittest
import logging
import sys
import requests
import os
import math
from pprint import pprint
from dotenv import load_dotenv
import telegram

COINGECKO_API = "https://api.coingecko.com/api/v3"
COINBASE_API = "https://api.exchange.coinbase.com"
COINBASE_URI = "wss://ws-feed.exchange.coinbase.com"

load_dotenv()
TELEGRAMBOTKEY = os.getenv("TELEGRAMBOTKEY")
bot = telegram.Bot(token=TELEGRAMBOTKEY)

"""
A monitor that detects unusual activitiy on coins
"""


class ActivityMonitor:
    def __init__(self):
        self.alerts = {}
        self.ALERT_THRESHOLD = 3
        self.THRESHOLD_ORDER_SIZE = 5000  # minimum order size
        self.THRESHOLD_VOL = 1000000  # min volume, changes get_threshold()
        self.ALERT_TIMEOUT = 1800 * 1  # alert timeout in seconds
        self.products = []
        self.product_data = {}
        self.blacklisted = ["RAI-USD"]
        self.coingecko_data = {}
        self._fetch_products()
        self._fetch_data()

        self._reset_alerts(self.products)
        self._fetch_marketcaps()

    def emit_bot_message(self, message):
        bot.send_message(
            text=message, chat_id="@CoinbaseWatcher", parse_mode="HTML")

    def get_alert_count(self, product, side):
        return len(self.alerts[product][side])

    def get_elapsed_time(self, alert, time_btwn_last_alert=False, seconds=False):
        """Get time difference between first and last alert
            * Seconds = False returns a formatted string [default]
            * Seconds = True returns an int of seconds between alerts
        """

        if len(alert) == 1:
            elapsed_time = int(time.time()-alert[0]['timestamp'])
        else:
            if time_btwn_last_alert:
                elapsed_time = int(time.time()-alert[-1]['timestamp'])
                print(elapsed_time)
            else:
                elapsed_time = int(
                    alert[-1]['timestamp'] - alert[0]['timestamp'])

        if elapsed_time < 1:
            elapsed_time = 1

        if seconds:
            return elapsed_time

        m, s = divmod(elapsed_time, 60)
        h, m = divmod(m, 60)

        if elapsed_time < 60:
            return f"{s}s"
        elif elapsed_time < 3600:
            return f"{m}m {s}s"
        else:
            return f"{h}h {m}m {s}s"

    def analyze(self, match):
        product = match['product_id']
        size = float(match['size'])
        price = float(match['price'])
        side = match['side']

        # For alert to trigger, buy/sell has to be > x% of volume
        total = price*size
        vol_24h = float(self.product_data[product]['volume'])*price
        threshold = min_order_size(
            self.THRESHOLD_ORDER_SIZE, vol_24h, self.THRESHOLD_VOL)

        if (total > threshold):
            print(f"{product} -> {side} -> ${total:,.0f}")

            # If time between last alert(s) for [side] > self.ALERT_TIMEOUT, reset. Make sure we have at least 1 alert
            if self.get_alert_count(product, side) > 0:
                last_alert_elapsed = self.get_elapsed_time(
                    self.alerts[product][side], time_btwn_last_alert=True, seconds=True)

                if last_alert_elapsed > self.ALERT_TIMEOUT:
                    self._reset_alerts(product)
            # Need n consecutive alerts to trigger message emission

            # if side == "sell" and self.get_alert_count(product, 'buy') > 0:
            #     self._reset_alerts(product)
            # if side == "buy" and self.get_alert_count(product, 'sell') > 0:
            #     self._reset_alerts(product)

            ts = time.time()
            self.alerts[product][side].append(
                {'size': size, 'price': price, 'total': total, 'timestamp': ts})

            if abs(self.get_alert_count(product, 'buy')-self.get_alert_count(product, 'sell')) >= self.ALERT_THRESHOLD:

                perc_change = percentage_change(price, float(
                    self.product_data[product]['open']))

                pnd_icon = {'sell': "💩", 'buy': "🚀"}

                activity_icon = {'sell': "🍟", 'buy': "✳️"}

                alert_elapsed_time = self.get_elapsed_time(
                    self.alerts[product][side])

                alert_total = sum_dicts(self.alerts[product][side], 'total')
                alert_size = sum_dicts(self.alerts[product][side], 'size')

                alert_msg = [emojifactory(pnd_icon[side], int(alert_total/30000)),
                             f"\t\t\t\t<b>{product}</b>",
                             f" {activity_icon[side]} {side.title()}ing activity in {alert_elapsed_time} {activity_icon[side]}\n"
                             f"\t 💸 Price: ${price:,} ({perc_change:.2f}%)",
                             f"\t 🎒 Size: {alert_size:,.2f} {product.split('-')[0]}",
                             f"\t 📊 {side.title()} Volume: ${alert_total:,.0f}",
                             f"\t 📊 24h Volume: ${vol_24h:,.0f}\n"]

                if self.coingecko_data and product in self.coingecko_data:
                    # API data can lag so multiply supply by latest price instead
                    cap = self.coingecko_data[product]['circulating_supply'] * price
                    if cap is not None and cap > 0:
                        cap_type = ""
                        if cap < 100000000:  # 100m
                            cap_type = "🍔 Low Cap 🍔"
                        elif cap < 1000000000:  # 1B
                            cap_type = "🎩 Mid Cap 🎩"
                        elif cap < 50000000000:  # 50B
                            cap_type = "🐳 Large Cap 🐳"
                        elif cap >= 50000000000:  # >50B
                            cap_type = "👑🏆 Elite 🏆👑"

                        if cap_type:
                            alert_msg.append(f"\t\t {cap_type}")

                        alert_msg.append(
                            f"\t 🏦 Market Cap: ${cap:,.0f}")

                    fdv = self.coingecko_data[product]['fdv']
                    if fdv is not None and fdv > 0:
                        alert_msg.append(f"\t 💰 FDV: ${fdv:,.0f}")

                    circ_supply = self.coingecko_data[product]['circulating_supply']
                    if circ_supply is not None and circ_supply > 0:
                        alert_msg.append(
                            f"\t ♻️ Circ Supply: {circ_supply:,.0f}")

                    total_supply = self.coingecko_data[product]['total_supply']
                    if total_supply is not None and total_supply > 0:
                        alert_msg.append(
                            f"\t 🪙 Total Supply: {total_supply:,.0f}")

                [print(m) for m in alert_msg]
                alertMessage = ""
                for i, m in enumerate(alert_msg):
                    if i == 0:
                        alertMessage = m
                    else:
                        alertMessage += f"\n\t{m}"

                self.emit_bot_message(alertMessage)

                # Update volume + other stats after alert is triggered
                self._reset_alerts([product])
                self._fetch_data(product)

    def _reset_alerts(self, products):
        for p in products:
            self.alerts[p] = {'buy': [], 'sell': []}

    def _fetch_products(self):
        start = time.process_time()

        url = f"{COINBASE_API}/products"
        self.products = api_data(url)  # get products
        self._filter_products()  # filter products for usd/stablecoins/delistings

        print(f"Done fetching products after {time.process_time() - start}s")

    def _filter_products(self):
        self.products = [p['id'] for p in self.products if p['fx_stablecoin'] ==
                         False and p['quote_currency'] == "USD" and p['trading_disabled'] == False and p['id'] not in self.blacklisted]

    def _fetch_data(self, product=None):
        """
        Fetch coinbase stats for each product. If a product is passed in, fetch stats only for that product.
        """

        if product:
            if product not in self.product_data:
                self.product_data[product] = {}
            ticker_data = self.fetch_stats(product)
            self._update_data(product, ticker_data)
        else:
            start = time.process_time()

            for p in self.products:
                if p not in self.product_data:
                    self.product_data[p] = {}

                ticker_data = self.fetch_stats(p)
                self._update_data(p, ticker_data)

            print(f"Done fetching stats after {time.process_time() - start}s")

    def fetch_stats(self, product):
        url = f"{COINBASE_API}/products/{product}/stats"
        return api_data(url)

    def _update_data(self, product, data):
        self.product_data[product].update(data)

    def _fetch_marketcaps(self):
        """
        Match coinbase products to coingecko market data
        """
        start = time.process_time()

        coinlist_url = f"{COINGECKO_API}/coins/list"
        coinlist = api_data(coinlist_url)
        cg_id_list = []
        for p in self.product_data.keys():
            symbol = p.split('-')[0]
            for c in coinlist:
                if symbol in [c['symbol'].upper(), c['name'].upper(), c['id'].upper()]:
                    self.coingecko_data[p] = {"coingecko_id": c['id']}
                    cg_id_list.append(c['id'])
                    break

        cg_ids = "%2C".join(cg_id_list)
        url = f"{COINGECKO_API}/coins/markets?vs_currency=usd&ids={cg_ids}"
        cg_market_data = api_data(url)

        for p in cg_market_data:
            product = f"{p['symbol'].upper()}-USD"
            if self.coingecko_data[product]['coingecko_id'] == p['id']:
                data_to_add = {
                    "market_cap": p['market_cap'], "circulating_supply": p["circulating_supply"], "total_supply": p["total_supply"], "max_supply": p["max_supply"],
                    "fdv": p["fully_diluted_valuation"], "ath": p["ath"], "ath_date": p["ath_date"], "ath_change_percentage": p["ath_change_percentage"], "atl": p["atl"],
                    "atl_date": p["atl_date"], "atl_change_percentage": p["atl_change_percentage"], "cg_last_updated": p['last_updated']}

                self.coingecko_data[product].update(data_to_add)
                # pprint(self.coingecko_data[product])

        print(
            f"Done fetching market caps after {time.process_time() - start}s")


def api_data(url, headers={"Accept": "application/json"}):
    try:
        response = requests.request("GET", url, headers=headers)
        if response.ok:
            return json.loads(response.text)
    except requests.exceptions.RequestException as e:
        print(e)

    return ""


def percentage_change(last_price, price):
    return round((last_price-price)/price, 2)*100


def emojifactory(emoji, count):
    return emoji*count if count > 0 else emoji


def sum_dicts(list_of_dicts, key):
    total = 0.0
    for d in list_of_dicts:
        total += d[key]
    return total


def min_order_size(order_size, volume, treshold_vol):

    if volume <= treshold_vol:  # 1m
        return order_size
    elif volume <= treshold_vol*10:  # 10m
        return order_size*2
    elif volume <= treshold_vol*25:  # 25m
        return order_size*3
    elif volume <= treshold_vol*100:  # 100m
        return order_size*6
    elif volume <= treshold_vol*300:  # 300m
        return order_size*7
    else:
        return order_size*25


"""
Main loop for consuming from the Websocket feed
"""


async def main_loop():
    monitor = ActivityMonitor()

    async with websockets.connect(COINBASE_URI, ping_interval=None, max_size=None) as websocket:
        auth_message = json.dumps({
            "type": "subscribe",
            "channels": ["matches"],
            "product_ids": monitor.products
        })
        await websocket.send(auth_message)

        try:
            while True:
                response = await websocket.recv()
                match = json.loads(response)

                if monitor and (match['type'] == "last_match" or match['type'] == "match"):
                    monitor.analyze(match)

        except websockets.exceptions.ConnectionClosedError:
            print("Error caught")
            sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main_loop())
