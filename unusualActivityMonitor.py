import asyncio
import datetime
import json
import hmac
import hashlib
import time
import base64
import asyncio
import time
import websockets
import unittest
import logging
import sys
import requests
import os
from pprint import pprint
from dotenv import load_dotenv
import telegram

"""
A monitor that detects unusual activitiy on coins
"""
COINGECKO_API = "https://api.coingecko.com/api/v3"
COINBASE_API = "https://api.exchange.coinbase.com"
COINBASE_URI = "wss://ws-feed.exchange.coinbase.com"

load_dotenv()
TELEGRAMBOTKEY = os.getenv("TELEGRAMBOTKEY")
bot = telegram.Bot(token=TELEGRAMBOTKEY)


class ActivityMonitor:
    def __init__(self):
        self.alerts = {}
        self.products = []
        self.product_data = {}
        self.blacklisted = ["RAI-USD"]
        self.coingecko_data = {}

        self._fetch_products()
        self._fetch_data()
        self._fetch_marketcaps()

    def emit_alert(self, message):
        bot.send_message(
            text=message, chat_id="@CoinbaseWatcher", parse_mode="HTML")

    def analyze(self, match):
        product = match['product_id']
        size = float(match['size'])
        price = float(match['price'])
        side = match['side']

        # For alert to trigger, buy/sell has to be > x% of volume
        total = price*size
        vol_24h = float(self.product_data[product]['volume'])*price
        threshold = get_threshold(vol_24h)

        if (total > (vol_24h*threshold)):
            perc_change = percentage_change(price, float(
                self.product_data[product]['open']))

            pnd = "Pumping" if match['side'] == "buy" else "Dumping"
            pnd_icon = "🚀" if match['side'] == "buy" else "👹"

            alert_msg = [f"🚨 {product} is {pnd} on Coinbase! {pnd_icon}",
                         f"\t 💸 Price: ${price:,} ({perc_change:.2f}%)",
                         f"\t 🎒 Size: {size:,.2f} {product.split('-')[0]}",
                         f"\t 📊 Volume of {side.upper()}: ${total:,.0f}",
                         f"\t 📊 24h Volume: ${vol_24h:,.0f}\n"]

            if self.coingecko_data and product in self.coingecko_data:
                cap = self.coingecko_data[product]['market_cap'] * price
                if cap is not None and cap > 0:
                    alert_msg.append(
                        f"\t 🏦 Market Cap: ${cap:,.0f}")

                fdv = self.coingecko_data[product]['fdv']
                if fdv is not None and fdv > 0:
                    alert_msg.append(f"\t 💰 FDV: ${fdv:,.0f}")

                circ_supply = self.coingecko_data[product]['circulating_supply']
                if circ_supply is not None and circ_supply > 0:
                    alert_msg.append(f"\t ♻️ Circ. Supply: {circ_supply:,.0f}")

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

            # Update volume + other stats after alert is triggered
            self._fetch_data(product)

            # alertMessage = f"🚨 {product} is {pnd} on Coinbase! {pnd_icon}\n\t\t 💸 Price: ${price} ({perc_change:.2f}%)\n\t\t  🎒 Size: {size:,.2f} {product.split('-')[0]}\n\t\t 📊 Volume of {side.upper()}: ${total:,.2f}\n\t\t 📊 24h Volume: ${vol_24h:,.2f}\n\t\t 📊 30d Volume: ${vol_30d:,.2f}"

            self.emit_alert(alertMessage)

        self._update_data(product, match, match=True)

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

    def _update_data(self, product, data, match=False):
        # If it's not a match, it's stats
        if match:
            self.product_data[product]['last'] = data['price']
        else:
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
    response = requests.request("GET", url, headers=headers)
    return json.loads(response.text)


def percentage_change(price, last_price):
    return round((price-last_price)/price, 2)*100


def get_threshold(volume):
    if volume <= 1000000:  # 1m
        return .015
    if volume <= 10000000:  # 10m
        return .005
    elif volume <= 100000000:  # 100m
        return .0025
    else:
        return .00175


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
                # if monitor != None:
                #     print(processor.stringify())
                #     sys.stdout.flush()
        except websockets.exceptions.ConnectionClosedError:
            print("Error caught")
            sys.exit(1)


if __name__ == '__main__':
    # products = fetch_products()
    # pprint(products)
    # fetch_volumes(products)
    asyncio.run(main_loop())
