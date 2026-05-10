# 羽毛球馆自动预约脚本

浙江警察学院羽毛球馆自动预约工具，通过 CAS 认证登录，自动查询和预约羽毛球场地。

## 功能

- **立即预约** — 登录后根据配置自动预约指定日期的场地
- **交互式预约** — 登录后查询时段列表，手动选择预约
- **定时预约** — 在指定时间自动登录并抢约场地，支持重试

## 环境要求

- Python 3.8+

## 安装

```bash
pip install -r requirements.txt
```

## 配置

编辑 `config.json` 文件：

```json
{
    "username": "你的学号",
    "password": "你的密码",
    "network_mode": "direct",
    "reserve": {
        "target_time": "19:50",
        "target_scene": "场次7",
        "preferred_court": 3,
        "days_offset": 3
    },
    "schedule": {
        "run_time": "08:59:58",
        "advance_login": 60,
        "max_retries": 10,
        "retry_interval": 0.5
    },
    "api_base": "https://attendence.yyhj.zjjcxy.cn/attendence-service",
    "cas_base": "https://cas.zjjcxy.cn",
    "venue_id": 999782
}
```

### 配置项说明

| 字段 | 说明 |
|------|------|
| `username` / `password` | CAS 登录账号密码 |
| `network_mode` | 网络模式：`direct`（内网直连）或 `webvpn`（WebVPN 代理） |
| `reserve.target_time` | 目标时段 |
| `reserve.target_scene` | 目标场次 |
| `reserve.preferred_court` | 首选场地编号 |
| `reserve.days_offset` | 预约几天后的场地 |
| `schedule.run_time` | 抢约执行时间 |
| `schedule.advance_login` | 提前多少秒登录 |
| `schedule.max_retries` | 最大重试次数 |
| `schedule.retry_interval` | 重试间隔（秒） |

### 网络模式

- **`direct`**（默认）— 在校园内网环境直接访问，适用于校内电脑
- **`webvpn`** — 通过学校 WebVPN 代理访问，适用于外网服务器（如腾讯云）

## 使用

```bash
python badminton.py
```

运行后选择模式：

```
1. 立即预约
2. 交互式预约
3. 定时预约
```

## 工作流程

1. 通过学校 CAS 系统认证登录
2. 获取 Ticket 并换取 API Token
3. 查询指定日期的场地时段
4. 根据配置筛选目标场地
5. 提交预约请求
