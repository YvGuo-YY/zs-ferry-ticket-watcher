# 嵊泗渡轮抢票系统

自动监控 [嵊泗轮船购票网](https://pc.ssky123.com) 的余票并自动购票，支持多账号、多任务并发，提供 Web 管理界面。

---

## 功能概览

- **自动抢票**：支持随机轮询或定时触发，按开售窗口自动进入刷票
- **双后端架构**：API 直连模式（快）和 Selenium 浏览器模式（兼容复杂场景）可随时切换
- **多账号管理**：支持多个渡轮购票账号，密码 Fernet 加密存储
- **旅客 / 车辆同步**：本地增删旅客和车辆时自动同步到所有渡轮账号的常用列表
- **客船 / 客滚船分流查询**：纯旅客任务走 `/line/ship/enq`，人车票任务走 `/line/ferry/enq`
- **超员拆单**：人车票超过 3 名旅客时可手动启用关联旅客子任务，主任务抢到车票后同班次下单
- **订单同步中心**：登录账号、手动刷新、订单页定时刷新都会同步远端订单和明细
- **实时日志**：WebSocket 推送任务执行日志到前端
- **Bark 推送通知**：支持先测试当前填写配置，再决定是否保存
- **Web 管理界面**：Vue 3 + Element Plus 单页应用，内置港口航线拓扑图

---

## 技术栈

| 层次 | 技术 |
|------|------|
| 后端框架 | FastAPI 0.111 + Uvicorn |
| 数据库 | SQLite（SQLAlchemy 2.0 同步） |
| 任务调度 | APScheduler 3.10（BackgroundScheduler） |
| API 后端 | 直接 HTTP 调用 pc.ssky123.com REST API |
| Selenium 后端 | Selenium 4 Remote WebDriver → Selenium Grid 4 |
| 前端 | Vue 3 CDN + Element Plus CDN（无构建步骤） |
| 推送通知 | Bark（iOS）|

---

## 项目结构

```
├── run.py                     # 启动入口（uvicorn --reload）
├── requirements.txt
├── pyproject.toml
├── uv.lock
├── docker/
│   └── docker-compose.yml     # Selenium Grid 4 + Chrome 节点
└── src/
    ├── main.py                # FastAPI 应用、WebSocket 实时日志
    ├── models.py              # ORM 模型
    ├── schemas.py             # Pydantic 请求/响应模型
    ├── database.py            # SQLite 初始化
    ├── auth.py                # JWT 鉴权
    ├── scheduler.py           # APScheduler 抢票任务调度
    ├── notify.py              # Bark 推送通知
    ├── api/
    │   ├── auth.py            # 登录 / 登出
    │   ├── users.py           # 系统用户管理
    │   ├── accounts.py        # 渡轮账号 CRUD + 登录测试 + 同步
    │   ├── passengers.py      # 旅客 CRUD（增删自动同步到远端）
    │   ├── vehicles.py        # 车辆 CRUD（增删自动同步到远端）
    │   ├── tasks.py           # 抢票任务 CRUD + 启动/停止 + 超员拆单
    │   ├── ports.py           # 港口航线查询
    │   ├── trips.py           # 手动查票 / 最远可购票日期
    │   ├── orders.py          # 订单列表 / 明细 / 远端同步
    │   └── settings.py        # 系统设置（爬虫模式、Bark 配置等）
    ├── crawler/
    │   ├── base.py            # 抽象后端接口
    │   ├── factory.py         # 后端工厂（读配置决定返回哪种实现）
    │   ├── api_backend.py     # API 直连后端
    │   ├── selenium_backend.py# Selenium 后端
    │   ├── login.py           # Selenium 登录逻辑
    │   ├── query.py           # Selenium 班次查询
    │   ├── booking.py         # Selenium 购票逻辑
    │   ├── sync_profile.py    # Selenium 同步常用旅客/车辆
    │   ├── driver.py          # WebDriver 工厂 + 健康检查
    │   └── session.py         # 密码 Fernet 加解密
    └── static/
        └── index.html         # Vue 3 SPA 前端
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动 Selenium Grid（仅 Selenium 模式需要）

```bash
docker compose -f docker/docker-compose.yml up -d
```

- Hub WebDriver 入口：`http://192.168.1.117:14444`
- noVNC 浏览器预览：`http://192.168.1.117:7900`

### 3. 启动应用

```bash
python run.py
```

或直接：

```bash
python -m uvicorn src.main:app --host 0.0.0.0 --port 8099 --reload
```

访问 `http://localhost:8099` 打开 Web 管理界面，默认管理员账号在首次启动时自动创建（见控制台输出）。

---

## 使用流程

1. **添加渡轮账号**：设置 → 渡轮账号，填入手机号和密码，点击"测试登录"验证
2. **添加旅客 / 车辆**：旅客管理 / 车辆管理，新增后自动同步到所有渡轮账号的常用列表
3. **创建抢票任务**：任务管理，选择出发港、目的港、日期、旅客、触发方式（随机轮询或定时开售）
4. **启动任务**：点击"启动"，实时日志窗口显示执行进度
5. **推送通知**：设置 → Bark，填入 Bark Key，可先点"测试"验证再保存
6. **订单管理**：订单页支持全部账号 / 单账号切换，手动刷新和每 1 分钟自动同步远端订单

---

## 抢票逻辑

### 定时与开售窗口

- `schedule` 任务会先排队到指定时间，到点后再进入正常开售规则
- 开售日当天 `06:40` 前：等待到 `06:40`
- 开售日当天 `06:40` 及以后：立即开始刷票
- 开售日之前：每天 `06:40` 检查是否进入开售窗口
- 每晚 `23:00` 后：暂停到次日 `06:40`

### 查询接口分流

- **纯旅客票主任务**：查询 `/api/v2/line/ship/enq`
- **人车票主任务**：查询 `/api/v2/line/ferry/enq`
- **超员拆单关联子任务**：不独立查票，等待主任务抢到车票后复用同班次直接下单

### 超员拆单

- 仅人车票支持启用超员拆单
- 启用条件：已选旅客数大于 3，且已从旅客中指定驾驶员
- 启用后：主任务只保留驾驶员，其余旅客进入关联旅客子任务
- 关联旅客子任务状态为 `waiting`，主任务抢到车票后自动触发同班次下单

### 日志说明

- 查询开始会明确显示所用接口：
  - `开始查询余票（人车票接口 /line/ferry/enq）`
  - `开始查询余票（旅客票接口 /line/ship/enq）`
- 未命中时会附带当前时段内班次、舱位余票和车位余票，便于判断到底是人票不足还是车位不足

---

## 订单同步

- 首次登录 Ferry 账号成功后，会立即同步一次远端订单
- 订单页支持手动刷新，并默认每 1 分钟自动刷新一次
- 支持“全部账号”或单账号查看
- 默认隐藏已取消订单，可通过顶部状态标签筛选
- 已支付订单支持查看远端 `orderItemList` 明细，包含：
  - `seatClassName`
  - `seatNumber`
  - `realFee`
  - `clxm`（航型）
  - `hxlxm`
  - `credentialNum`
  - `passName`
  - `lineName`
  - `createTime`

---

## 爬虫模式

在 **设置 → 爬虫模式** 中切换：

| 模式 | 说明 | 适用场景 |
|------|------|---------|
| `api`（默认） | 直接调用 REST API，速度快，无需浏览器 | 日常使用、订单同步、快速查票 |
| `selenium` | 通过 Selenium Grid 控制 Chrome | API 不可用或需要处理验证码 |

两种模式共享同一套接口（`CrawlerBackend`），切换无需修改任务配置。

> **注意**：订单同步当前仅在 API 模式下支持直连远端接口；若切换为 Selenium 模式，订单页不会额外去官网拉取新订单。

---

## 旅客 / 车辆双向同步

- **本地新增**：提交后在后台自动 `POST /api/v2/user/passenger/save`（或 `/vehicle/save`）到所有渡轮账号
- **本地删除**：提交后在后台自动调用删除接口，从远端常用列表中移除
- **从渡轮账号同步**：账号管理 → 同步，从远端拉取常用旅客/车辆写入本地（去重）
- **去重逻辑**：推送前先查远端列表，按证件号（旅客）/ 车牌号（车辆）比对，已存在则跳过

---

## 系统设置

| 键 | 说明 |
|----|------|
| `crawler_backend` | 爬虫模式：`api` / `selenium` |
| `bark_key` | Bark 推送 Key |
| `bark_server` | Bark 服务器地址（默认 `https://api.day.app`） |

---

## API 文档

启动后访问 `http://localhost:8099/docs` 查看 Swagger 交互文档。

主要端点：

```
POST   /api/auth/login              登录，返回 JWT token
GET    /api/accounts/               渡轮账号列表
POST   /api/accounts/{id}/login     测试登录
POST   /api/accounts/{id}/sync      同步常用旅客/车辆
GET    /api/passengers/             旅客列表
POST   /api/passengers/             新增旅客（自动同步远端）
DELETE /api/passengers/{id}         删除旅客（自动同步远端）
GET    /api/vehicles/               车辆列表
POST   /api/vehicles/               新增车辆（自动同步远端）
DELETE /api/vehicles/{id}           删除车辆（自动同步远端）
GET    /api/tasks/                  任务列表
POST   /api/tasks/                  新建任务
POST   /api/tasks/{id}/start        启动任务
POST   /api/tasks/{id}/stop         停止任务
WS     /ws/tasks/{id}/logs          实时日志 WebSocket
GET    /api/ports/                  港口航线列表
POST   /api/trips/query             手动查票（支持旅客票 / 人车票）
GET    /api/orders/                 订单列表（支持账号与状态筛选）
GET    /api/orders/{id}/detail      订单详情
POST   /api/orders/sync             同步远端订单
GET    /api/settings/               系统设置
PUT    /api/settings/               更新系统设置
POST   /api/settings/test-bark      测试 Bark 推送
GET    /api/health                  健康检查
GET    /api/selenium/health         Selenium Grid 健康检查
```
