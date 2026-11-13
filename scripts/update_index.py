#!/usr/bin/env python3
"""Discover public HAP release assets and generate README/Pages data.

The script intentionally uses only Python's standard library so it can run on
the default GitHub Actions runner without installing dependencies.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_CONFIG = {
    "search_queries": [
        "topic:harmonyos",
        "topic:openharmony",
        "HarmonyOS hap",
        "OpenHarmony hap",
    ],
    "seed_repositories": [],
    "excluded_repositories": [],
    "max_repositories_per_query": 40,
    "max_releases_per_repository": 100,
    "include_prereleases": True,
    "readme_max_rows": 200,
}


class GitHubAPIError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None, api_base: str) -> None:
        self.token = token
        self.api_base = api_base.rstrip("/")
        self.user_agent = "open-harmony-hap-index/1.0"

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = f"?{urlencode(params)}" if params else ""
        request = Request(f"{self.api_base}{path}{query}")
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", self.user_agent)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            raise GitHubAPIError(
                f"GitHub API {error.code} for {path}: {body[:300]}"
            ) from error
        except URLError as error:
            raise GitHubAPIError(f"GitHub API network error for {path}: {error}") from error


def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return DEFAULT_CONFIG.copy()
    with path.open(encoding="utf-8") as handle:
        loaded = json.load(handle)
    config = DEFAULT_CONFIG.copy()
    config.update(loaded)
    return config


def search_repositories(
    client: GitHubClient, query: str, limit: int
) -> tuple[list[dict[str, Any]], bool]:
    """Return repositories for a search query and whether the request succeeded."""
    results: list[dict[str, Any]] = []
    remaining = max(0, limit)
    page = 1
    succeeded = False

    while remaining > 0:
        per_page = min(100, remaining)
        payload = client.get_json(
            "/search/repositories",
            {
                "q": query,
                "sort": "updated",
                "order": "desc",
                "per_page": per_page,
                "page": page,
            },
        )
        succeeded = True
        items = payload.get("items", [])
        results.extend(items)
        if len(items) < per_page:
            break
        remaining -= len(items)
        page += 1
    return results[:limit], succeeded


def list_releases(
    client: GitHubClient, repository: str, limit: int
) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    remaining = max(0, limit)
    page = 1

    while remaining > 0:
        per_page = min(100, remaining)
        items = client.get_json(
            f"/repos/{repository}/releases",
            {"per_page": per_page, "page": page},
        )
        releases.extend(items)
        if len(items) < per_page:
            break
        remaining -= len(items)
        page += 1
    return releases[:limit]


def repository_metadata(repository: dict[str, Any]) -> dict[str, Any]:
    license_info = repository.get("license") or {}
    return {
        "name": repository.get("name") or repository.get("full_name", "").split("/")[-1],
        "full_name": repository.get("full_name", ""),
        "url": repository.get("html_url", ""),
        "description": repository.get("description") or "",
        "stars": repository.get("stargazers_count", 0),
        "license": license_info.get("spdx_id") or license_info.get("name") or "",
        "default_branch": repository.get("default_branch", ""),
    }


def discover_apps(client: GitHubClient, config: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = {str(item).lower() for item in config.get("excluded_repositories", [])}
    repositories: dict[str, dict[str, Any]] = {}
    successful_searches = 0
    successful_seed_fetches = 0

    for query in config.get("search_queries", []):
        try:
            found, succeeded = search_repositories(
                client,
                str(query),
                int(config.get("max_repositories_per_query", 40)),
            )
            successful_searches += int(succeeded)
            for repository in found:
                full_name = str(repository.get("full_name", ""))
                if not full_name or full_name.lower() in excluded:
                    continue
                if repository.get("archived") or repository.get("fork"):
                    continue
                repositories[full_name] = repository
            print(f"search: {query} -> {len(found)} repositories")
        except GitHubAPIError as error:
            print(f"warning: search failed for {query!r}: {error}", file=sys.stderr)

    for full_name in config.get("seed_repositories", []):
        repository_name = str(full_name).strip()
        if not repository_name or repository_name.lower() in excluded:
            continue
        try:
            repository = client.get_json(f"/repos/{repository_name}")
            successful_seed_fetches += 1
            if not repository.get("archived") and not repository.get("fork"):
                repositories[repository_name] = repository
        except GitHubAPIError as error:
            print(f"warning: seed repository failed for {repository_name}: {error}", file=sys.stderr)

    if not successful_searches and not successful_seed_fetches:
        raise GitHubAPIError("all repository searches failed; keeping existing data")

    apps: dict[tuple[str, str], dict[str, Any]] = {}
    release_limit = int(config.get("max_releases_per_repository", 30))
    successful_release_fetches = 0

    for index, (full_name, repository) in enumerate(sorted(repositories.items()), start=1):
        try:
            releases = list_releases(client, full_name, release_limit)
            successful_release_fetches += 1
        except GitHubAPIError as error:
            print(f"warning: releases failed for {full_name}: {error}", file=sys.stderr)
            continue

        metadata = repository_metadata(repository)
        for release in releases:
            if release.get("draft"):
                continue
            if release.get("prerelease") and not config.get("include_prereleases", True):
                continue
            for asset in release.get("assets", []):
                asset_name = str(asset.get("name", ""))
                if not asset_name.lower().endswith(".hap"):
                    continue
                download_url = asset.get("browser_download_url", "")
                key = (full_name.lower(), download_url or asset_name.lower())
                apps[key] = {
                    "app_name": metadata["name"],
                    "repository": metadata["full_name"],
                    "repository_url": metadata["url"],
                    "description": metadata["description"],
                    "license": metadata["license"],
                    "stars": metadata["stars"],
                    "version": release.get("tag_name") or release.get("name") or "",
                    "release_name": release.get("name") or "",
                    "release_url": release.get("html_url", ""),
                    "published_at": release.get("published_at") or release.get("created_at") or "",
                    "prerelease": bool(release.get("prerelease")),
                    "asset_name": asset_name,
                    "download_url": download_url,
                    "size": asset.get("size", 0),
                    "digest": asset.get("digest") or "",
                    "asset_updated_at": asset.get("updated_at") or asset.get("created_at") or "",
                }
        if index % 10 == 0:
            print(f"releases: checked {index}/{len(repositories)} repositories")

    if repositories and not successful_release_fetches:
        raise GitHubAPIError("all release requests failed; keeping existing data")

    return sorted(
        apps.values(),
        key=lambda app: (
            app.get("published_at", "") or "",
            app.get("repository", ""),
            app.get("asset_name", ""),
        ),
        reverse=True,
    )


def format_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return "-"


def markdown_cell(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ").strip()


def apps_table(apps: list[dict[str, Any]], max_rows: int) -> str:
    if not apps:
        return "暂无发现 `.hap` Release asset。"
    rows = [
        "| 应用 / 仓库 | 版本 | HAP 文件 | 大小 | 发布日期 |",
        "|---|---|---|---:|---|",
    ]
    for app in apps[:max_rows]:
        repository = markdown_cell(app["repository"])
        repository_url = app["repository_url"]
        release_url = app["release_url"]
        repository_link = f"[{repository}]({repository_url})" if repository_url else repository
        asset = markdown_cell(app["asset_name"])
        asset_link = f"[{asset}]({app['download_url']})" if app["download_url"] else asset
        version = markdown_cell(app["version"])
        version_cell = f"[{version}]({release_url})" if release_url and version else version
        published = markdown_cell(app["published_at"][:10])
        rows.append(
            f"| {repository_link} | {version_cell} | {asset_link} | {format_size(app['size'])} | {published} |"
        )
    if len(apps) > max_rows:
        rows.append("")
        rows.append(f"> README 仅显示最新 {max_rows} 条，完整列表请查看在线网页。")
    return "\n".join(rows)


def render_readme(template_path: Path, apps: list[dict[str, Any]], max_rows: int) -> str:
    template = template_path.read_text(encoding="utf-8") if template_path.exists() else (
        "# OpenHarmony / HarmonyOS HAP Index\n\n"
        "<!-- APPS:START -->\n<!-- APPS:END -->\n"
    )
    repository_slug = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repository_slug:
        owner, repository_name = repository_slug.split("/", 1)
        template = template.replace(
            "https://YOUR-USER.github.io/YOUR-REPOSITORY/",
            f"https://{owner}.github.io/{repository_name}/",
        )
    start = "<!-- APPS:START -->"
    end = "<!-- APPS:END -->"
    if start not in template or end not in template:
        raise ValueError(f"README template must contain {start} and {end}")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    block = (
        f"<!-- APPS:START -->\n"
        f"最近更新：`{generated_at}`，共发现 **{len(apps)}** 个 HAP 文件。\n\n"
        f"{apps_table(apps, max_rows)}\n"
        f"<!-- APPS:END -->"
    )
    before, remainder = template.split(start, 1)
    _, after = remainder.split(end, 1)
    return before + block + after


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--template", type=Path, default=None)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir or root
    config_path = args.config or root / "config.json"
    template_path = args.template or root / "README.template.md"
    config = load_config(config_path)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        print("warning: GITHUB_TOKEN is not set; GitHub API limits will be lower", file=sys.stderr)
    api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    client = GitHubClient(token, api_base)

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    apps = discover_apps(client, config)
    payload = {
        "generated_at": started_at,
        "count": len(apps),
        "apps": apps,
    }

    write_json(output_dir / "data" / "apps.json", payload)
    write_json(output_dir / "site" / "data" / "apps.json", payload)
    readme = render_readme(template_path, apps, int(config.get("readme_max_rows", 200)))
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    print(f"generated {len(apps)} HAP assets")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (GitHubAPIError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
