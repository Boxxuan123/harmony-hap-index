# OpenHarmony / HarmonyOS HAP Index

自动发现 GitHub 公开仓库 Release 中的 `.hap` 安装包。项目只保存索引和原始链接，不镜像安装包。

- 在线目录：GitHub Pages 部署后可访问
- 更新频率：GitHub Actions 每天一次，也可以手动触发
- 数据来源：公开 GitHub 仓库及其 Release assets

<!-- APPS:START -->
最近更新：尚未运行自动索引。

暂无发现 `.hap` Release asset。
<!-- APPS:END -->

## 使用方法

1. 将仓库公开，并在仓库 Settings → Pages 中允许 GitHub Actions 发布。
2. 检查 `config.json` 中的搜索词和数量限制。
3. 手动运行一次 `Update HAP index`，之后由每日定时任务自动更新。

系统根据配置中的搜索词发现候选仓库，然后读取这些仓库的 Releases 并筛选文件名以 `.hap` 结尾的附件。GitHub 搜索无法保证覆盖所有公开仓库，因此欢迎通过 Pull Request 提交遗漏的仓库；可以把仓库加入 `seed_repositories`。

请在使用安装包前自行核对项目源码、许可证、发布者和文件来源。索引页面保留原始仓库与 Release 链接。
