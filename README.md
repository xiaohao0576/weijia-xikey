# Sales Visits PWA

手机端销售工具。功能和第一版范围见 [requirement-v2.md](requirement-v2.md)。

## 前置条件

- Odoo SaaS 19.4 或 20.0，允许使用 External JSON-2 API；配置的集成账号需要读取 `hr.employee`（包括 `pin`、`active` 和 `work_contact_id`）、读写 `res.partner`、读取 `res.partner.category`、创建 `ir.attachment` 和调用 `message_post` 的权限。每位打卡员工必须配置工作联系人；打卡消息的作者为该联系人，缺少联系人时不会创建附件或消息。
- Odoo 已配置 `res.partner.x_employee_id`、所需语言及标签。项目使用 `res.partner.zip` 保存 9 位 geoHash，不保留原邮编用途；附近查询同时兼容以前保存的 7 位和 8 位 geoHash，不会自动修改旧记录。已有客户只有具备本应用兼容的 geoHash 和坐标才能出现在附近查询中。
- 启用 Google Maps JavaScript API，配置浏览器可使用的 Key 和域名限制；Google 地图使用情况可能产生费用。
- 手机能通过 HTTPS 访问应用；Caddy 负责 HTTPS 和反向代理。实际手机和 Odoo 环境需要由 IT 验证。

## Docker 部署

1. 将 `.env.example` 复制为 `.env`，填写 Odoo URL、数据库名、API Key、Google Maps 浏览器 Key 和足够随机的 `SESSION_SECRET`。不要提交 `.env`。
2. 执行 `docker compose up -d --build`。应用在宿主机监听 `127.0.0.1:8083`，将 Caddy 的反向代理上游地址也修改为该地址，以转发外部 HTTPS 请求。
3. Compose 的 `app_data` 卷保存提交状态 SQLite 数据库。重新部署不要删除此卷；备份时应使用 SQLite 在线备份或先停止应用后备份卷中的 `operations.sqlite3`，恢复后确认文件权限。
4. IT 在员工手机访问 `https://你的域名/`，然后安装到桌面并验证桌面启动、员工选择、PIN、定位、地图和相机。首次使用时选择员工并输入 PIN，验证成功后应用在该浏览器或 PWA 环境中记住员工；之后只需输入 PIN。浏览器和已安装 PWA 可能使用不同的网站数据，需分别测试首次登录。

员工信息保存在签名 Cookie 中，有效期为一年；闲置会话仍在一小时后过期。清除对应网站数据或 Cookie 失效后需要重新选择员工。旧安装链接中的员工 ID 参数不再生效；此前已成功登录且 Cookie 仍有效的用户会继续使用已记住的员工。本应用不提供退出或切换员工按钮；需要更换员工时，由 IT 清除对应浏览器或 PWA 的网站数据，再重新选择。

`COOKIE_SECURE=true` 适用于 HTTPS 生产环境。本机使用纯 HTTP 开发时可设为 `false`；不要在生产环境关闭。`DATA_DIR` 指定数据库目录，默认 `data`。`GET /health` 只检查进程可响应，不代表 Odoo 和 Google Maps 可用。应用日志不应包含 PIN 或 API Key。

本地开发示例：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
# 设置 .env；本地 HTTP 使用 COOKIE_SECURE=false
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8083
.venv/bin/python -m pytest -q tests
```

## 不确定结果的人工核查

客户创建和打卡不会在超时后自动重试。页面显示的参考编号对应持久化日志；`pending` 表示请求已登记但尚无最终结果，可能仍在处理，也可能进程已中断。**在确认原请求不再运行之前，不得将其判定为失败并重试。** 查询数据库：

```sh
docker compose exec app python manage_operations.py 参考编号
```

在 Odoo 中按员工、店铺或客户、时间检查；查打卡时也检查附件和 chatter。日志中 `attachment_ids` 是已确认或响应中已返回的附件 ID；附件创建响应丢失时可能没有 ID，仍须在 Odoo 检查。消息明确失败时可能已有附件，重试前也应检查和处理这些附件。确认客户创建成功并取得客户 ID 后：

```sh
docker compose exec app python manage_operations.py 参考编号 --resolve success --partner-id 123 --detail "Verified customer 123 in Odoo"
```

确认打卡成功：

```sh
docker compose exec app python manage_operations.py 参考编号 --resolve success --detail "Verified message in Odoo"
```

确认没有产生目标记录，且原请求不会再完成后，可标记失败，再由销售员重新提交：

```sh
docker compose exec app python manage_operations.py 参考编号 --resolve failed --detail "Verified no record and original request stopped"
```

无法确认时保持原状态。已确认成功或失败的记录不能通过此命令再次更改。客户页面刷新后可恢复非图片字段；出于浏览器限制，照片需要重新选择。已离开创建页面后不能从应用重新打开编辑。

## 验证与限制

客户照片和打卡照片选择后会显示缩略图，可点击缩略图上的删除按钮移除，再通过加号选择图片。客户创建成功后，缩略图会在当前页面保留；移除已保存的照片后，需要点击更新客户才能从 Odoo 删除。打卡成功后仍会返回首页。

自动测试使用模拟 Odoo，不替代真实 SaaS 19.4/20.0 上的 API、权限、WebP、消息附件、以及真实手机安装与定位测试。

打卡时先用 TimeMark 拍摄，通过单个相册字段选择 1～3 张原图，每张最多 20 MiB，支持 JPEG、PNG、WebP，不支持 HEIC。照片不经浏览器压缩，以保留元数据，通过 JSON-2 的 `ir.attachment.raw` 以 Base64 传输，因此上传耗时和流量可能较大；反向代理也需允许至少 85 MiB 请求体。服务端依据样张中的 EXIF `Software` 值识别照片：值中任意位置包含 `timemark`（忽略大小写）即通过；尚未确认其他 TimeMark 版本或 XMP 标记。元数据可伪造或被分享、编辑过程删除，不能证明照片来源或现场到访；无法识别时请重新选择原图或使用 [TimeMark 下载链接](https://landing.timemark.com/go?us=official_website&um=direct&uc=owqr_zh-hant)。相册是否保留 EXIF 和多选体验须在目标手机上验证。

Google Maps 需要网络，应用不支持离线提交。geoHash 查询覆盖约 3 公里范围所在的网格，网格边缘可能包含更远客户；最多返回 100 条，未保证按距离排序。地图缺少 API 权限或设备拒绝定位时会提示错误。
