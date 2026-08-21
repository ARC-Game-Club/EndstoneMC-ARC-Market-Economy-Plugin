import datetime
import os

from endstone.command import Command, CommandSender
from endstone.plugin import Plugin

from .DatabaseManager import DatabaseManager
from .LanguageManager import LanguageManager
from .SettingManager import SettingManager
from .PriceManager import PriceManager


class ARCMarketEconomyPlugin(Plugin):
    """弧光市场经济：全服共享官方价目与动态调价。"""

    prefix = "ARCMarketEconomyPlugin"
    api_version = "0.10"
    load = "POSTWORLD"

    PLACEHOLDER_SELL_PRICE = 0

    commands = {
        "market": {
            "description": "ARC market economy admin commands",
            "usages": [
                "/market",
                "/market prices",
                "/market reload",
                "/market reset",
            ],
            "permissions": ["arc_market_economy.command.market"],
        }
    }

    permissions = {
        "arc_market_economy.command.market": {
            "description": "Allow OP market economy management",
            "default": "op",
        }
    }

    def __init__(self):
        super().__init__()
        self.db_manager = None
        self.setting_manager = None
        self.language_manager = None
        self.price_manager = None

    def _safe_log(self, level: str, message: str):
        if hasattr(self, "logger") and self.logger is not None:
            fn = getattr(self.logger, level.lower(), None) or self.logger.info
            fn(message)
        else:
            print(f"[{level.upper()}] {message}")

    def on_load(self) -> None:
        self._safe_log("info", "[ARCMarketEconomy] on_load")
        self.language_manager = LanguageManager("CN")
        self.setting_manager = SettingManager()
        self._init_default_settings()

        db_path = os.path.join("plugins", "ARCMarketEconomy", "market_economy.db")
        self.db_manager = DatabaseManager(db_path)
        self._create_tables()

        self.price_manager = PriceManager(self)
        self.price_manager.load_price_adjustments_from_db(self.db_manager)

        # 确保模板价目可被复制（LanguageManager 同类逻辑：首次启动写空文件后由 PriceManager 建默认）
        self._ensure_bundled_price_template()

    def on_enable(self) -> None:
        self._safe_log("info", "[ARCMarketEconomy] on_enable")
        self._register_scheduled_tasks()

    def on_disable(self) -> None:
        self._safe_log("info", "[ARCMarketEconomy] on_disable")
        try:
            if hasattr(self, "server") and self.server:
                self.server.scheduler.cancel_tasks(self)
        except Exception as e:
            self._safe_log("error", f"[ARCMarketEconomy] cancel tasks: {e}")
        if self.db_manager:
            self.db_manager.close()

    def _ensure_bundled_price_template(self):
        """若运行时价目为空且目录无文件，PriceManager 已建默认；此处仅记日志。"""
        count = len(self.price_manager.official_prices) if self.price_manager else 0
        self._safe_log("info", f"[ARCMarketEconomy] Official priced items: {count}")

    def _init_default_settings(self):
        defaults = {
            "dynamic_pricing_enabled": "true",
            "dynamic_pricing_sell_amount_per_percent": "10000",
            "dynamic_pricing_buy_amount_per_percent": "10000",
            "dynamic_pricing_max_sell_increase": "0.50",
            "dynamic_pricing_max_buy_decrease": "0.30",
            "dynamic_pricing_sell_buy_link_ratio": "0.5",
            "dynamic_pricing_recovery_rate_per_hour": "0.0002",
            "daily_fluctuation_enabled": "true",
            # 已废弃：全日波动，不再按数量抽样
            "daily_fluctuation_item_count": "0",
            "daily_fluctuation_min_percent": "-15",
            "daily_fluctuation_max_percent": "15",
            "daily_fluctuation_reset_hour": "0",
        }
        for key, value in defaults.items():
            if self.setting_manager.GetSetting(key) is None:
                self.setting_manager.SetSetting(key, value)

    def _create_tables(self):
        self.db_manager.create_table(
            "item_trade_volume",
            {
                "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
                "item_type": "TEXT NOT NULL",
                "trade_type": "TEXT NOT NULL",
                "quantity": "INTEGER NOT NULL",
                "total_amount": "REAL NOT NULL DEFAULT 0",
                "trade_time": "TEXT NOT NULL",
                "source": "TEXT NOT NULL DEFAULT ''",
            },
        )
        self.db_manager.create_table(
            "price_adjustments",
            {
                "item_type": "TEXT PRIMARY KEY",
                "demand_sell_adjust": "REAL NOT NULL DEFAULT 0",
                "demand_buy_adjust": "REAL NOT NULL DEFAULT 0",
                "daily_adjust_percent": "REAL NOT NULL DEFAULT 0",
                "sell_amount_accumulated": "REAL NOT NULL DEFAULT 0",
                "buy_amount_accumulated": "REAL NOT NULL DEFAULT 0",
                "sell_link_adjust": "REAL NOT NULL DEFAULT 0",
                "last_updated": "TEXT NOT NULL",
            },
        )
        # 兼容旧列
        self._migrate_columns()

    def _migrate_columns(self):
        try:
            rows = self.db_manager.query_all("PRAGMA table_info(item_trade_volume)")
            names = [r["name"] for r in (rows or [])]
            if "total_amount" not in names:
                self.db_manager.execute(
                    "ALTER TABLE item_trade_volume ADD COLUMN total_amount REAL NOT NULL DEFAULT 0"
                )
            if "source" not in names:
                self.db_manager.execute(
                    "ALTER TABLE item_trade_volume ADD COLUMN source TEXT NOT NULL DEFAULT ''"
                )
            rows = self.db_manager.query_all("PRAGMA table_info(price_adjustments)")
            names = [r["name"] for r in (rows or [])]
            for col, decl in (
                ("sell_amount_accumulated", "REAL NOT NULL DEFAULT 0"),
                ("buy_amount_accumulated", "REAL NOT NULL DEFAULT 0"),
                ("sell_link_adjust", "REAL NOT NULL DEFAULT 0"),
            ):
                if col not in names:
                    self.db_manager.execute(
                        f"ALTER TABLE price_adjustments ADD COLUMN {col} {decl}"
                    )
        except Exception as e:
            self._safe_log("error", f"[ARCMarketEconomy] migrate columns: {e}")

    def _register_scheduled_tasks(self):
        try:
            scheduler = self.server.scheduler
            self._daily_task = scheduler.run_task(
                self, self._daily_fluctuation_task, delay=1200, period=1728000
            )
            self._recovery_task = scheduler.run_task(
                self, self._price_recovery_task, delay=0, period=72000
            )
            self._cleanup_task = scheduler.run_task(
                self, self._cleanup_task_handler, delay=6000, period=1728000
            )
            self._safe_log("info", "[ARCMarketEconomy] Scheduled tasks registered")
        except Exception as e:
            self._safe_log("error", f"[ARCMarketEconomy] register tasks: {e}")

    def _daily_fluctuation_task(self):
        try:
            self.price_manager.check_and_apply_daily_fluctuation(self.db_manager)
        except Exception as e:
            self._safe_log("error", f"[ARCMarketEconomy] daily task: {e}")

    def _price_recovery_task(self):
        try:
            self.price_manager.apply_price_recovery(self.db_manager)
        except Exception as e:
            self._safe_log("error", f"[ARCMarketEconomy] recovery task: {e}")

    def _cleanup_task_handler(self):
        try:
            self.price_manager.cleanup_old_trade_volumes(self.db_manager, days=7)
        except Exception as e:
            self._safe_log("error", f"[ARCMarketEconomy] cleanup task: {e}")

    # ==================== Commands ====================

    def on_command(self, sender: CommandSender, command: Command, args: list[str]) -> bool:
        if command.name != "market":
            return True
        if not getattr(sender, "is_op", False):
            sender.send_message(self.language_manager.GetText("NO_PERMISSION") or "§cNo permission")
            return True
        if not args:
            sender.send_message(self.language_manager.GetText("MARKET_USAGE"))
            return True
        sub = args[0].lower()
        if sub == "prices":
            return self._cmd_prices(sender)
        if sub == "reload":
            n = self.api_reload_prices()
            sender.send_message(self.language_manager.GetText("MARKET_RELOAD_SUCCESS").format(n))
            return True
        if sub == "reset":
            self.api_reset_adjustments()
            sender.send_message(self.language_manager.GetText("MARKET_RESET_SUCCESS"))
            return True
        sender.send_message(self.language_manager.GetText("MARKET_USAGE"))
        return True

    def _cmd_prices(self, sender) -> bool:
        status = self.api_get_market_status()
        dyn = "ON" if status.get("dynamic_pricing_enabled") else "OFF"
        daily = "ON" if status.get("daily_fluctuation_enabled") else "OFF"
        sender.send_message(
            self.language_manager.GetText("MARKET_PRICES_CONTENT").format(
                status.get("item_count", 0), dyn, daily
            ).replace("\\n", "\n")
        )
        summary = self.price_manager.get_daily_fluctuation_summary()
        if summary:
            sender.send_message(summary)
        return True

    # ==================== Public API ====================

    def api_has_price(self, item_type: str) -> bool:
        return bool(self.price_manager and self.price_manager.has_official_price(item_type))

    def api_ensure_item(
        self,
        item_type: str,
        sell: int = 0,
        buy: int = 0,
        display_name: str = None,
        category: str = "待配置",
    ) -> dict:
        """确保价目存在；缺失则自动写入（默认 sell/buy=0）。"""
        if not self.price_manager or not item_type:
            return {}
        return self.price_manager.ensure_item_price(
            item_type, sell=sell, buy=buy, display_name=display_name, category=category
        )

    def api_get_base_price(self, item_type: str, side: str):
        if not self.price_manager:
            return None
        # 询价时自动补全缺失物品
        self.api_ensure_item(item_type, sell=0, buy=0)
        return self.price_manager.get_base_price(item_type, side)

    def api_get_final_price(self, item_type: str, side: str, discount_percent: float = 0.0):
        if not self.price_manager:
            return None
        self.api_ensure_item(item_type, sell=0, buy=0)
        return self.price_manager.calculate_final_price(item_type, side, discount_percent)

    def api_is_buy_suspended(self, item_type: str, discount_percent: float = 0.0) -> bool:
        if not self.price_manager:
            return True
        return self.price_manager.is_buy_disabled(item_type, discount_percent)

    def api_get_display_name(self, item_type: str) -> str:
        if not self.price_manager:
            return item_type or "?"
        return self.price_manager.get_item_display_name(item_type)

    def api_list_priced_items(self) -> dict:
        if not self.price_manager:
            return {}
        return self.price_manager.get_all_priced_items()

    def api_get_categories(self) -> list:
        if not self.price_manager:
            return []
        return self.price_manager.get_category_order()

    def api_get_items_by_category(self, category: str) -> dict:
        if not self.price_manager:
            return {}
        return self.price_manager.get_items_by_category(category)

    def api_get_category_counts(self) -> dict:
        if not self.price_manager:
            return {}
        return self.price_manager.get_category_counts()

    def api_get_adjustment(self, item_type: str) -> dict:
        if not self.price_manager:
            return {}
        return self.price_manager.get_price_adjustment(item_type)

    def api_get_quote(self, item_type: str, side: str, discount_percent: float = 0.0):
        if not self.price_manager:
            return None
        base = self.price_manager.get_base_price(item_type, side)
        final = self.price_manager.calculate_final_price(item_type, side, discount_percent)
        adj = self.price_manager.get_price_adjustment(item_type)
        return {
            "item_type": item_type,
            "side": side,
            "base": base,
            "final": final,
            "discount_percent": discount_percent,
            "demand_adj": adj.get(
                "demand_sell_adjust" if side == "sell" else "demand_buy_adjust", 0.0
            ),
            "daily_pct": adj.get("daily_adjust_percent", 0.0),
            "link_adj": adj.get("sell_link_adjust", 0.0) if side == "buy" else 0.0,
            "suspended": final is None and side == "buy",
            "display_name": self.price_manager.get_item_display_name(item_type),
        }

    def api_resolve_item_type(self, held_type_id: str):
        """将手持物品 id 解析为 official_prices 键。"""
        if not held_type_id or not self.price_manager:
            return None
        keys = {held_type_id}
        if ":" in held_type_id:
            keys.add(held_type_id.split(":", 1)[1])
        else:
            keys.add(f"minecraft:{held_type_id}")
        for official_type in self.price_manager.official_prices:
            okeys = {official_type}
            if ":" in official_type:
                okeys.add(official_type.split(":", 1)[1])
            else:
                okeys.add(f"minecraft:{official_type}")
            if keys & okeys:
                return official_type
        return None

    def api_on_trade(
        self,
        item_type: str,
        side: str,
        quantity: int,
        total_amount: float,
        source: str = "",
    ) -> None:
        """成交成功后调用：记流水并更新需求定价。"""
        if not self.price_manager or not self.db_manager:
            return
        try:
            # record_trade_volume 内部已写流水；额外记 source（若列存在）
            self.price_manager.record_trade_volume(
                item_type, side, int(quantity), self.db_manager, float(total_amount or 0)
            )
            if source:
                try:
                    self.db_manager.execute(
                        "UPDATE item_trade_volume SET source = ? WHERE id = ("
                        "SELECT id FROM item_trade_volume ORDER BY id DESC LIMIT 1)",
                        (str(source),),
                    )
                except Exception:
                    pass
            self.price_manager.update_demand_pricing(item_type, self.db_manager)
        except Exception as e:
            self._safe_log("error", f"[ARCMarketEconomy] api_on_trade: {e}")

    def api_reload_prices(self) -> int:
        if not self.price_manager:
            return 0
        self.price_manager.reload_config()
        self.price_manager.load_price_adjustments_from_db(self.db_manager)
        return len(self.price_manager.official_prices)

    def api_reset_adjustments(self) -> None:
        if self.price_manager and self.db_manager:
            self.price_manager.reset_all_adjustments(self.db_manager)

    def api_get_market_status(self) -> dict:
        sm = self.setting_manager
        return {
            "item_count": len(self.price_manager.official_prices) if self.price_manager else 0,
            "dynamic_pricing_enabled": bool(
                sm and sm.GetSettingBool("dynamic_pricing_enabled", True)
            ),
            "daily_fluctuation_enabled": bool(
                sm and sm.GetSettingBool("daily_fluctuation_enabled", True)
            ),
            "placeholder_sell_price": self.PLACEHOLDER_SELL_PRICE,
        }
