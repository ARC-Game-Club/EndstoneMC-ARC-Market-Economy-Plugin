# 弧光市场经济插件 (ARC Market Economy)

[![版本](https://img.shields.io/badge/版本-0.1.0-blue.svg)](https://github.com/DEVILENMO/EndstoneMC-ARC-Market-Economy-Plugin)
[![EndStone](https://img.shields.io/badge/EndStone-0.10+-green.svg)](https://github.com/EndstoneMC/endstone)

全服共享的官方价目与动态市场：供木牌商店等插件询价与反馈成交。

## 功能

- `official_prices.yml` 统一基准价（sell / buy）
- **每日波动**：价目表内**全部物品**各自随机波动（不再只抽 N 个）
- **需求调价**：买卖成交累计金额驱动出售价上涨 / 回收价下跌，并支持出售-回收联动
- **价格回归**：需求偏离按小时缓慢回到基准
- 回收价 ≥ 出售价时自动暂停回收

## 指令（OP）

| 指令 | 说明 |
|------|------|
| `/market prices` | 查看价目数量与开关状态 |
| `/market reload` | 重载 `official_prices.yml` |
| `/market reset` | 清空所有动态调整 |

## 配置

数据目录：`plugins/ARCMarketEconomy/`

- `official_prices.yml` — 基准价
- `core_setting.yml` — `dynamic_pricing_*`、`daily_fluctuation_*`
- `market_economy.db` — 流水与调整态

`daily_fluctuation_item_count` 已废弃（兼容保留），实现始终对全部价目物品施加日波动。

## 供其他插件调用

```python
mkt = server.plugin_manager.get_plugin("arc_market_economy")
price = mkt.api_get_final_price("minecraft:diamond", "sell", discount_percent=0)
mkt.api_on_trade("minecraft:diamond", "sell", quantity=1, total_amount=10000, source="sign_shop")
```

主要 API：`api_has_price`、`api_get_base_price`、`api_get_final_price`、`api_get_quote`、`api_list_priced_items`、`api_get_categories`、`api_on_trade`、`api_reload_prices`、`api_reset_adjustments`。
