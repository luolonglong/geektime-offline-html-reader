# -*- coding: utf-8 -*-
# !/usr/bin/python
"""=========================================
@author: Tenma
@file: geek_crawler.py
@create_time: 2020/4/23 12:29
@file specification: 极客时间专栏下载脚本 — 生成离线 HTML 阅读版（本地图片，不写入音视频）
    极客时间官网地址：https://time.geekbang.org/
    流程：登录账号 -- 获取专栏列表 -- 下载文章正文与图片 -- 写入独立 HTML 阅读版目录
========================================="""
import time
import datetime
import argparse
import getpass
import requests
import re
import hashlib
from copy import deepcopy
import logging
import os
import pathlib
import html as html_lib
from urllib.parse import quote as url_quote

# 定义日志相关内容
logging.basicConfig(format='%(asctime)s - %(pathname)s[line:%(lineno)d] - %(levelname)s: %(message)s',
                    level=logging.INFO)
handler = logging.FileHandler(filename='geek_crawler.log', mode='w', encoding='utf-8')
log = logging.getLogger(__name__)
log.addHandler(handler)

# 定义全局变量
FINISH_ARTICLES = []
ALL_ARTICLES = []
HTML_READING_SUFFIX = '_HTML阅读版'
DEFAULT_COURSES = ('AI Agent 系统设计面试现场',)


class RequestError(Exception):
    """ 请求错误 """
    pass


class NotValueError(Exception):
    """ 没有内容错误 """
    pass


def _load_finish_article():
    """ 将当前目录下已遍历过文章 ID 文件中的数据加载到内存中 """
    result = []
    _dir = pathlib.PurePosixPath()
    file_path = os.path.abspath(_dir / 'finish_crawler_article.txt')
    if os.path.exists(file_path):
        with open(file_path, 'r', encoding='utf-8') as f:
            for article_id in f.readlines():
                article_id = article_id.strip('\n')
                if article_id:
                    result.append(article_id)
    return list(set(result))


def _save_finish_article_id_to_file():
    """ 将已经遍历完成的文章 ID 保存成文本，后面不用再遍历 """
    global FINISH_ARTICLES
    _dir = pathlib.PurePosixPath()
    file_path = os.path.abspath(_dir / 'finish_crawler_article.txt')
    with open(file_path, 'a+', encoding='utf-8') as f:
        for i in FINISH_ARTICLES:
            f.write(str(i) + '\n')


def check_filename(file_name):
    """
    校验文件名称的方法，在 windows 中文件名不能包含('\','/','*','?','<','>','|') 字符
    Args:
        file_name: 文件名称
    Returns:
        修复后的文件名称
    """
    return file_name.replace('\\', '') \
        .replace('/', '') \
        .replace('*', 'x') \
        .replace('?', '') \
        .replace('<', '《') \
        .replace('>', '》') \
        .replace('|', '_') \
        .replace('\n', '') \
        .replace('\b', '') \
        .replace('\f', '') \
        .replace('\t', '') \
        .replace('\r', '')


def _build_local_recommendations(dir_path, current_filename):
    """生成指向同目录其他文章的轻量阅读导航。"""
    names = set()
    if os.path.isdir(dir_path):
        names.update(name for name in os.listdir(dir_path)
                     if name.lower().endswith('.html')
                     and name.lower() != 'index.html')
    names.add(current_filename)
    ordered = sorted(names, key=str.casefold)
    try:
        current_index = ordered.index(current_filename)
    except ValueError:
        return ''

    nearby = ordered[max(0, current_index - 2):current_index]
    nearby += ordered[current_index + 1:current_index + 4]
    if not nearby:
        return ''

    links = []
    for name in nearby:
        label = os.path.splitext(name)[0]
        links.append(
            f'<a href="{url_quote(name)}">{html_lib.escape(label)}</a>'
        )
    return '<nav class="related"><h2>继续阅读</h2><div class="related-grid">' + \
        ''.join(links) + '</div></nav>'


_IMAGE_VIEWER_SCRIPT = r'''<script>
(() => {
  const article = document.querySelector('.article-content');
  const viewer = document.getElementById('image-viewer');
  const viewerImage = viewer.querySelector('.viewer-image');
  const closeButton = viewer.querySelector('.viewer-close');
  let lastImage = null;

  const openViewer = (image) => {
    lastImage = image;
    viewerImage.src = image.currentSrc || image.src;
    viewerImage.alt = image.alt || '';
    viewer.classList.add('is-open');
    viewer.setAttribute('aria-hidden', 'false');
    document.body.classList.add('viewer-open');
    closeButton.focus();
  };

  const closeViewer = () => {
    viewer.classList.remove('is-open');
    viewer.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('viewer-open');
    viewerImage.removeAttribute('src');
    if (lastImage) lastImage.focus();
  };

  article.querySelectorAll('img').forEach((image) => {
    image.tabIndex = 0;
    image.setAttribute('role', 'button');
    image.setAttribute('aria-label', image.alt ? `点击放大图片：${image.alt}` : '点击放大图片');
  });

  article.addEventListener('click', (event) => {
    const image = event.target instanceof Element ? event.target.closest('img') : null;
    if (!image) return;
    event.preventDefault();
    openViewer(image);
  });

  article.addEventListener('keydown', (event) => {
    const image = event.target instanceof Element ? event.target.closest('img') : null;
    if (!image || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    openViewer(image);
  });

  viewer.addEventListener('click', (event) => {
    if (event.target === viewer ||
        (event.target instanceof Element && event.target.closest('[data-viewer-close]'))) {
      closeViewer();
    }
  });

  viewer.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeViewer();
    if (event.key === 'Tab') {
      event.preventDefault();
      closeButton.focus();
    }
  });
})();
</script>'''


_ARTICLE_OUTLINE_SCRIPT = r'''<script>
(() => {
  const article = document.querySelector('.article-content');
  const panel = document.getElementById('article-outline-panel');
  const details = panel.querySelector('details');
  const list = panel.querySelector('.outline-list');
  const headings = Array.from(article.querySelectorAll('h1, h2, h3, h4, h5, h6'))
    .filter((heading) => heading.textContent.trim());

  if (!headings.length) {
    panel.hidden = true;
    return;
  }

  const compact = window.matchMedia('(max-width: 1320px)');
  details.open = !compact.matches;
  const usedIds = new Set();
  const links = new Map();

  headings.forEach((heading, index) => {
    const baseId = heading.id || 'article-section-' + (index + 1);
    let id = baseId;
    let suffix = 2;
    while (usedIds.has(id) ||
           (document.getElementById(id) && document.getElementById(id) !== heading)) {
      id = baseId + '-' + suffix++;
    }
    heading.id = id;
    heading.style.scrollMarginTop = '24px';

    const item = document.createElement('li');
    item.className = 'outline-level-' + heading.tagName.toLowerCase();
    const link = document.createElement('a');
    link.href = '#' + encodeURIComponent(id);
    link.textContent = heading.textContent.trim().replace(/\s+/g, ' ');
    link.title = link.textContent;
    link.addEventListener('click', () => {
      setActive(id);
      if (compact.matches) details.open = false;
    });
    item.appendChild(link);
    list.appendChild(item);
    usedIds.add(id);
    links.set(id, link);
  });

  function setActive(id) {
    links.forEach((link, headingId) => {
      const active = headingId === id;
      link.classList.toggle('is-active', active);
      if (active) link.setAttribute('aria-current', 'location');
      else link.removeAttribute('aria-current');
    });
  }

  let framePending = false;
  const updateActive = () => {
    let active = headings[0];
    for (const heading of headings) {
      if (heading.getBoundingClientRect().top <= 150) active = heading;
      else break;
    }
    setActive(active.id);
    framePending = false;
  };
  window.addEventListener('scroll', () => {
    if (framePending) return;
    framePending = true;
    window.requestAnimationFrame(updateActive);
  }, { passive: true });
  window.addEventListener('hashchange', updateActive);
  window.addEventListener('resize', () => {
    details.open = !compact.matches;
  });
  updateActive();
})();
</script>'''


def _render_reading_page(title, content, related_html='', author=''):
    """把文章片段包装成适合离线阅读的独立 HTML 页面。"""
    safe_title = html_lib.escape(title)
    author_html = (
        f'<div class="author">{html_lib.escape(author)}</div>' if author else ''
    )
    # 阅读版只保留文章正文和图片，不渲染音频、视频播放器。
    content = re.sub(r'(?is)<(audio|video)\b[^>]*>.*?</\1\s*>', '', content)
    content = re.sub(r'(?is)</?(?:audio|video)\b[^>]*>', '', content)
    return f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    html {{ scroll-behavior: smooth; }}
    body {{ margin: 0; background: #fff; color: #555; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }}
    .reader {{ width: min(864px, calc(100% - 36px)); margin: 52px auto 72px; }}
    .article-outline {{ position: fixed; top: 132px; left: max(16px, calc((100vw - 864px) / 2 - 220px)); z-index: 20; width: 188px; max-height: calc(100vh - 148px); overflow: hidden; border-radius: 6px; background: #f8f9fb; color: #666; font-size: 13px; }}
    .article-outline[hidden] {{ display: none; }}
    .article-outline summary {{ display: flex; align-items: center; justify-content: space-between; padding: 13px 12px; color: #444; font-size: 14px; font-weight: 600; cursor: pointer; list-style: none; }}
    .article-outline summary::-webkit-details-marker {{ display: none; }}
    .article-outline summary::after {{ content: "⌄"; color: #999; font-size: 14px; }}
    .article-outline details[open] summary::after {{ content: "⌃"; }}
    .article-outline nav {{ max-height: calc(100vh - 198px); overflow-y: auto; overscroll-behavior: contain; }}
    .outline-list {{ margin: 0; padding: 0 8px 12px; list-style: none; }}
    .outline-list a {{ display: block; padding: 6px 7px; border-radius: 4px; color: #78808c; font-size: 13px; line-height: 1.5; text-decoration: none; }}
    .outline-list a:hover, .outline-list a.is-active {{ background: #fff1e5; color: #f18725; }}
    .outline-level-h3 a {{ padding-left: 17px; }}
    .outline-level-h4 a {{ padding-left: 27px; }}
    .outline-level-h5 a {{ padding-left: 37px; }}
    .outline-level-h6 a {{ padding-left: 47px; }}
    .article-header h1 {{ margin: 0; color: #333; font-size: 26px; font-weight: 500; line-height: 1.5; }}
    .author {{ margin-top: 7px; color: #999; font-size: 14px; }}
    .article-content {{ margin-top: 30px; color: #666; font-size: 15px; line-height: 1.8; overflow-wrap: anywhere; }}
    .article-content p {{ margin: 0 0 19px; }}
    .article-content h1, .article-content h2, .article-content h3, .article-content h4 {{ margin: 26px 0 14px; color: #333; font-weight: 600; line-height: 1.55; }}
    .article-content h1 {{ font-size: 22px; }}
    .article-content h2 {{ font-size: 20px; }}
    .article-content h3 {{ font-size: 18px; }}
    .article-content strong, .article-content b {{ color: #444; }}
    .article-content img {{ display: block; width: auto; max-width: 100%; height: auto; margin: 20px auto; object-fit: contain; cursor: zoom-in; }}
    .article-content img:focus-visible {{ outline: 2px solid #ee8d25; outline-offset: 3px; }}
    .article-content figure {{ max-width: 100%; margin: 22px auto; text-align: center; }}
    .article-content figure img {{ margin: 0 auto; }}
    .article-content table {{ display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; }}
    .article-content pre {{ max-width: 100%; overflow-x: auto; padding: 14px; background: #f6f7f9; border-radius: 4px; line-height: 1.6; }}
    .article-content blockquote {{ margin: 18px 0; padding: 2px 0 2px 14px; border-left: 3px solid #e5e7eb; color: #777; }}
    .related {{ margin-top: 34px; padding: 14px 18px 18px; border: 3px solid #ffddc1; border-radius: 10px; }}
    .related h2 {{ display: inline-block; margin: -1px 0 12px; padding: 3px 12px; border-radius: 0 0 6px 6px; background: #fff0e2; color: #d5893e; font-size: 14px; font-weight: 600; }}
    .related-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px 24px; }}
    .related a {{ color: #555; font-size: 13px; line-height: 1.6; text-decoration: none; }}
    .related a:hover {{ color: #ee8d25; }}
    .reader-footer {{ margin-top: 18px; padding-top: 12px; border-top: 1px solid #eee; color: #aaa; font-size: 12px; }}
    body.viewer-open {{ overflow: hidden; }}
    .image-viewer {{ position: fixed; inset: 0; z-index: 9999; display: flex; align-items: center; justify-content: center; padding: 32px; background: rgba(0, 0, 0, .88); opacity: 0; visibility: hidden; pointer-events: none; transition: opacity .18s ease, visibility .18s ease; }}
    .image-viewer.is-open {{ opacity: 1; visibility: visible; pointer-events: auto; }}
    .viewer-image {{ display: block; width: auto; max-width: calc(100vw - 64px); max-height: calc(100vh - 64px); height: auto; margin: 0; object-fit: contain; box-shadow: 0 8px 40px rgba(0, 0, 0, .4); cursor: zoom-out; }}
    .viewer-close {{ position: fixed; top: 12px; right: 18px; width: 44px; height: 44px; border: 0; border-radius: 50%; background: rgba(255, 255, 255, .15); color: #fff; font-size: 30px; line-height: 1; cursor: pointer; }}
    .viewer-close:hover, .viewer-close:focus-visible {{ background: rgba(255, 255, 255, .28); outline: 2px solid #fff; }}
    @media (max-width: 640px) {{
      .reader {{ width: calc(100% - 32px); margin-top: 30px; }}
      .article-header h1 {{ font-size: 22px; }}
      .article-content {{ margin-top: 24px; font-size: 15px; }}
      .related-grid {{ grid-template-columns: 1fr; }}
    }}
    @media (max-width: 1320px) {{
      .article-outline {{ position: static; width: min(864px, calc(100% - 36px)); max-height: none; margin: 18px auto 0; overflow: visible; }}
      .article-outline nav {{ max-height: min(38vh, 280px); }}
      .reader {{ margin-top: 24px; }}
    }}
    @media (prefers-reduced-motion: reduce) {{ html {{ scroll-behavior: auto; }} }}
  </style>
</head>
<body>
  <aside id="article-outline-panel" class="article-outline" aria-label="文章大纲">
    <details open>
      <summary>大纲</summary>
      <nav aria-label="文章标题导航"><ol class="outline-list"></ol></nav>
    </details>
  </aside>
  <main class="reader">
    <header class="article-header"><h1>{safe_title}</h1>{author_html}</header>
    <article class="article-content">{content}</article>
    {related_html}
    <footer class="reader-footer"><a href="index.html">专栏目录</a> · 极客时间专栏本地 HTML 阅读版</footer>
  </main>
  <div id="image-viewer" class="image-viewer" role="dialog" aria-modal="true" aria-label="图片预览" aria-hidden="true">
    <button class="viewer-close" type="button" data-viewer-close aria-label="关闭图片预览">×</button>
    <img class="viewer-image" alt="">
  </div>
  {_IMAGE_VIEWER_SCRIPT}
  {_ARTICLE_OUTLINE_SCRIPT}
</body>
</html>'''


def _write_local_index(dir_path, course_title, author=''):
    """更新本地专栏目录，方便离线浏览已保存的文章。"""
    names = sorted(
        (name for name in os.listdir(dir_path)
         if name.lower().endswith('.html') and name.lower() != 'index.html'),
        key=str.casefold
    )
    if not names:
        return

    links = []
    for name in names:
        label = os.path.splitext(name)[0]
        links.append(
            f'<li><a href="{url_quote(name)}">{html_lib.escape(label)}</a></li>'
        )
    links_html = ''.join(links)
    safe_course_title = html_lib.escape(str(course_title))
    author_html = (
        f'<div class="author">{html_lib.escape(author)} · 共 {len(names)} 篇</div>'
        if author else f'<div class="author">共 {len(names)} 篇</div>'
    )
    index_html = f'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_course_title} · 专栏目录</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #fff; color: #555; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif; }}
    .reader {{ width: min(864px, calc(100% - 36px)); margin: 52px auto 72px; }}
    h1 {{ margin: 0; color: #333; font-size: 26px; font-weight: 500; line-height: 1.5; }}
    .author {{ margin-top: 7px; color: #999; font-size: 14px; }}
    ol {{ margin: 30px 0 0; padding: 0; list-style: none; }}
    li {{ padding: 13px 0; border-bottom: 1px solid #eee; }}
    a {{ color: #555; font-size: 15px; line-height: 1.7; text-decoration: none; }}
    a:hover {{ color: #ee8d25; }}
    @media (max-width: 640px) {{ .reader {{ width: calc(100% - 32px); margin-top: 30px; }} h1 {{ font-size: 22px; }} }}
  </style>
</head>
<body>
  <main class="reader">
    <header><h1>{safe_course_title}</h1>{author_html}</header>
    <ol>{links_html}</ol>
  </main>
</body>
</html>'''
    with open(os.path.join(dir_path, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(index_html)


class Cookie:
    def __init__(self, cookie_string=None):
        self._cookies = {}
        if cookie_string:
            self.load_string_cookie(cookie_string)

    @property
    def cookie_string(self):
        return ';'.join([f'{k}={v}' for k, v in self._cookies.items()])

    def set_cookie(self, key, value):
        self._cookies[key] = value

    @staticmethod
    def list_to_dict(lis):
        result = {}
        for ind in lis:
            try:
                ind = ind.split('=')
                result[ind[0]] = ind[1]
            except IndexError:
                continue
        return result

    def load_string_cookie(self, cookie_str):
        cookie_list = cookie_str.split(';')
        res = self.list_to_dict(cookie_list)
        self._cookies = {**self._cookies, **res}

    def load_set_cookie(self, set_cookie):
        set_cookie = re.sub(".xpires=.*?;", "", set_cookie)
        cookies_list = set_cookie.split(',')
        cookie_list = []
        for cookie in cookies_list:
            cookie_list.append(cookie.split(';')[0])
        res = self.list_to_dict(cookie_list)
        self._cookies = {**self._cookies, **res}

    def __repr__(self):
        return f'The cookies is : {self._cookies}'


class GeekCrawler:
    """ 极客时间相关操作的类 """

    def __init__(self, cellphone=None, passwd=None, only=None, download_assets=False, cookie_string=None):
        self.cellphone = cellphone
        self.password = passwd
        self.cookie_string = cookie_string
        if not cookie_string:
            self._check()
        # Cookie 初始化：如果传入了 cookie_string 则直接使用，否则用默认空壳
        init_cookie = cookie_string if cookie_string else ""
        self.cookie = Cookie(init_cookie)
        self.common_headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Pragma": "no-cache",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_13_6) "
                          "AppleWebKit/537.36 (KHTML, like Gecko)Chrome/81.0.4044.122 Safari/537.36"
        }
        self.products = []
        self.only = only if only else []
        self.download_assets = download_assets

    def _check(self):
        if not self.cellphone:
            self.cellphone = os.environ.get('GEEKTIME_CELLPHONE') or input('极客时间手机号: ').strip()
        if not self.password:
            self.password = os.environ.get('GEEKTIME_PASSWORD') or getpass.getpass('极客时间密码: ')

    def _login(self):
        """ 登录方法：如果传入了 cookie_string 则直接使用，否则走手机号+密码登录 """
        if self.cookie_string:
            log.info("使用传入的 Cookie 登录，跳过手机号认证。")
            self.cookie.load_string_cookie(self.cookie_string)
            log.info('-' * 40)
            return

        log.info("请求登录接口：")
        url = "https://account.geekbang.org/account/ticket/login"
        method = "POST"
        headers = deepcopy(self.common_headers)
        headers["Host"] = "account.geekbang.org"
        headers["Origin"] = "https://account.geekbang.org"
        headers["Cookie"] = self.cookie.cookie_string
        params = {
            "country": 86,
            "cellphone": self.cellphone,
            "password": self.password,
            "captcha": "",
            "remember": 1,
            "platform": 3,
            "appid": 1,
            "source": ""
        }

        safe_params = {**params, "cellphone": "[REDACTED]", "password": "[REDACTED]"}
        log.info(f"接口请求参数：{safe_params}")
        res = requests.request(method, url, headers=headers, json=params)

        if (res.status_code != 200) or (str(res.json().get('code', '')) == '-1'):
            _save_finish_article_id_to_file()
            log.info(f"此时 products 的数据为：{self.products}")
            log.error(f"登录接口请求出错，返回内容为：{res.content.decode()}")
            raise RequestError(f"登录接口请求出错，返回内容为：{res.content.decode()}")
        self.cookie.load_set_cookie(res.headers['Set-Cookie'])
        log.info('-' * 40)

    def _user_auth(self):
        """ 用户认证接口方法 """
        log.info("请求用户认证接口：")
        now_time = int(time.time() * 1000)
        url = f"https://account.geekbang.org/serv/v1/user/auth?t={now_time}"
        method = "GET"
        headers = deepcopy(self.common_headers)
        headers["Host"] = "account.geekbang.org"
        headers["Origin"] = "https://time.geekbang.org"
        headers["Cookie"] = self.cookie.cookie_string

        res = requests.request(method, url, headers=headers)

        if (res.status_code != 200) or (str(res.json().get('code', '')) != '0'):
            _save_finish_article_id_to_file()
            log.info(f"此时 products 的数据为：{self.products}")
            log.error(f"用户认证接口请求出错，返回内容为：{res.json()}")
            raise RequestError(f"用户认证接口请求出错，返回内容为：{res.json()}")
        self.cookie.load_set_cookie(res.headers['Set-Cookie'])
        log.info('-' * 40)

    def _product(self, _type='c1'):
        """ 商品列表（就是课程）的接口）方法 """
        log.info("请求获取课程列表接口：")
        url = "https://time.geekbang.org/serv/v3/learn/product"
        method = "POST"
        headers = deepcopy(self.common_headers)
        headers["Host"] = "time.geekbang.org"
        headers["Origin"] = "https://time.geekbang.org"
        headers["Cookie"] = self.cookie.cookie_string
        params = {
            "desc": 'true',
            "expire": 1,
            "last_learn": 0,
            "learn_status": 0,
            "prev": 0,
            "size": 20,
            "sort": 1,
            "type": "",
            "with_learn_count": 1
        }

        log.info(f"接口请求参数：{params}")
        res = requests.request(method, url, headers=headers, json=params)

        if res.status_code != 200:
            log.info(f"此时 products 的数据为：{self.products}")
            log.error(f"课程列表接口请求出错，返回内容为：{res.content.decode()}")
            raise RequestError(f"课程列表接口请求出错，返回内容为：{res.content.decode()}")
        data = res.json().get('data', {})
        self.cookie.load_set_cookie(res.headers['Set-Cookie'])

        if data:
            self.products += self._parser_products(data, _type)
        else:
            _save_finish_article_id_to_file()
            log.info(f"此时 products 的数据为：{self.products}")
            log.error(f"课程列表接口没有获取到内容，请检查请求。返回结果为：{res.content.decode()}")
            raise NotValueError(f"课程列表接口没有获取到内容，请检查请求。返回结果为：{res.content.decode()}")
        log.info('-' * 40)

    def _parser_products(self, data, _type='c1'):
        """
        解析课程列表内容的方法（从中提取部分数据）
        Args:
            data: 课程相关信息，一般为接口返回的数据
            _type: 课程类型，c1 代表专栏，all 代表全部, 默认只获取专栏的内容
        Returns:
            解析后的结果，以列表形式
        """
        result = []
        keys = ['title', 'type', 'id']
        products = data.get('products', [])
        lists = data.get('list', [])
        for product in products:
            # only 列表非空时，只抓取列表中指定的课程；为空时抓取全部
            if self.only and product.get('title', '') not in self.only:
                continue

            new_product = {key: value for key, value in product.items() if key in keys}
            new_product['articles'] = []
            new_product['article_ids'] = []
            for pro in lists:
                if new_product['id'] == pro['pid']:
                    new_product['aid'] = pro['aid']
            # self.only 非空时跳过类型过滤（用户已明确指定课程），否则按 _type 过滤
            if self.only or _type.lower() == 'all' or new_product['type'] == _type:
                result.append(new_product)
        return result

    def _article(self, aid, pro, file_type=None, get_comments=False):
        """ 通过课程 ID 获取文章信息接口方法 """
        global FINISH_ARTICLES
        log.info("请求获取文章信息接口：")
        url = "https://time.geekbang.org/serv/v1/article"
        method = "POST"
        headers = deepcopy(self.common_headers)
        headers["Host"] = "time.geekbang.org"
        headers["Origin"] = "https://time.geekbang.org"
        headers["Cookie"] = self.cookie.cookie_string
        params = {
            "id": aid,
            "include_neighbors": "true",
            "is_freelyread": "true"
        }

        log.info(f"接口请求参数：{params}")
        res = requests.request(method, url, headers=headers, json=params)

        if res.status_code != 200:
            _save_finish_article_id_to_file()
            log.info(f"此时 products 的数据为：{self.products}")
            log.error(f"获取文章信息接口请求出错，返回内容为：{res.content.decode()}")
            raise RequestError(f"获取文章信息接口请求出错，返回内容为：{res.content.decode()}")
        data = res.json().get('data', {})
        log.info(f"API 返回的所有字段: {list(data.keys())}")
        self.cookie.load_set_cookie(res.headers['Set-Cookie'])

        if data:
            comments = self._comments(aid) if get_comments else None
            content_keys = ['article_content', 'article_title', 'id', 'audio_download_url', 'audio_url']
            video_keys = ['video_media_map', 'video_cover', 'video_id',
                          'video_width', 'video_height', 'hls_videos']
            keys = content_keys + video_keys
            article = {key: value for key, value in data.items() if key in keys}

            # 构建视频信息（只保留有值的字段）
            video_info = {k: v for k, v in article.items() if k in video_keys and v}
            if not video_info:
                video_info = None

            self.save_to_file(
                pro['title'],
                article['article_title'],
                article['article_content'],
                audio=article.get('audio_url') or article.get('audio_download_url'),
                file_type=file_type,
                comments=comments,
                video_info=video_info
            )

            FINISH_ARTICLES.append(article['id'])
            pro['cid'] = data['cid']
        else:
            _save_finish_article_id_to_file()
            log.info(f"此时 products 的数据为：{self.products}")
            log.error(f"获取文章信息接口没有获取到内容，请检查请求。返回结果为：{res.content.decode()}")
            raise NotValueError(f"获取文章信息接口没有获取到内容，请检查请求。返回结果为：{res.content.decode()}")
        log.info('-' * 40)

    def _comments(self, aid):
        """ 获取文章评论详情接口 """
        log.info("请求获取文章评论详情接口：")
        url = "https://time.geekbang.org/serv/v1/comments"
        method = "POST"
        headers = deepcopy(self.common_headers)
        headers["Host"] = "time.geekbang.org"
        headers["Origin"] = "https://time.geekbang.org"
        headers["Cookie"] = self.cookie.cookie_string
        params = {
            "aid": aid,
            "prev": "0"
        }

        log.info(f"接口请求参数：{params}")
        res = requests.request(method, url, headers=headers, json=params)

        if res.status_code != 200:
            log.error(f"获取文章评论接口请求出错，返回内容为：{res.content.decode()}")
            return None
        data = res.json().get('data', {}).get('list', [])
        self.cookie.load_set_cookie(res.headers['Set-Cookie'])

        if data:
            keys = ['comment_content', 'comment_ctime', 'user_header', 'user_name', 'replies']
            comments = [{key: value for key, value in comment.items() if key in keys} for comment in data]
            return comments
        else:
            return None

    def _articles(self, cid, pro):
        """ 获取文章列表接口方法 """
        global ALL_ARTICLES
        log.info("请求获取文章列表接口：")
        url = "https://time.geekbang.org/serv/v1/column/articles"
        method = "POST"
        headers = deepcopy(self.common_headers)
        headers["Host"] = "time.geekbang.org"
        headers["Origin"] = "https://time.geekbang.org"
        headers["Cookie"] = self.cookie.cookie_string
        params = {
            "cid": cid,
            "size": 100,
            "prev": 0,
            "order": "earliest",
            "sample": "false"
        }

        log.info(f"接口请求参数：{params}")
        res = requests.request(method, url, headers=headers, json=params)

        if res.status_code != 200:
            _save_finish_article_id_to_file()
            log.info(f"此时 products 的数据为：{self.products}")
            log.error(f"获取文章列表接口请求出错，返回内容为：{res.json()}")
            raise RequestError(f"获取文章列表接口请求出错，返回内容为：{res.json()}")
        data = res.json().get('data', {})
        self.cookie.load_set_cookie(res.headers['Set-Cookie'])

        if data:
            ids = []
            article_list = data.get('list', [])
            for article in article_list:
                ids.append(article['id'])
            ALL_ARTICLES += ids
            pro['article_ids'] += ids
        else:
            _save_finish_article_id_to_file()
            log.info(f"此时 products 的数据为：{self.products}")
            log.error(f"获取文章列表接口没有获取到内容，请检查请求。返回结果为：{res.json()}")
            raise NotValueError(f"获取文章列表接口没有获取到内容，请检查请求。返回结果为：{res.json()}")
        log.info('-' * 40)

    # ==================== 多媒体资源下载 ====================

    def _extract_filename_from_url(self, url, resp=None):
        """
        从 URL 提取文件名；无效时用 URL hash 生成。
        """
        clean_url = url.split('?')[0]
        filename = clean_url.rstrip('/').split('/')[-1]
        if '#' in filename:
            filename = filename.split('#')[0]

        if not filename or '.' not in filename:
            ext = '.jpg'
            if resp and resp.headers.get('Content-Type'):
                ct = resp.headers['Content-Type'].split(';')[0].strip()
                ext_map = {
                    'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
                    'image/webp': '.webp', 'image/svg+xml': '.svg',
                    'video/mp4': '.mp4', 'video/webm': '.webm',
                    'audio/mpeg': '.mp3', 'audio/mp4': '.m4a', 'audio/ogg': '.ogg',
                }
                ext = ext_map.get(ct, '.jpg')
            filename = hashlib.md5(url.encode()).hexdigest()[:12] + ext

        return check_filename(filename)

    def _download_single_url(self, url, assets_dir):
        """
        下载单个 URL 到 assets 目录，返回本地文件名（不含路径），失败返回 None。
        """
        if not url:
            return None
        if url.startswith('//'):
            url = 'https:' + url
        if not url.startswith(('http://', 'https://')):
            return None

        dl_headers = {
            "Referer": "https://time.geekbang.org/",
            "User-Agent": self.common_headers["User-Agent"]
        }
        try:
            resp = requests.get(url, headers=dl_headers, timeout=60)
            if resp.status_code == 200:
                filename = self._extract_filename_from_url(url, resp)
                filepath = os.path.join(assets_dir, filename)
                if os.path.exists(filepath):
                    return filename
                with open(filepath, 'wb') as f:
                    f.write(resp.content)
                log.info(f"  资源下载成功: {filename}")
                return filename
            else:
                log.warning(f"  资源下载失败 [{resp.status_code}]: {url}")
                return None
        except Exception as e:
            log.warning(f"  资源下载异常: {url} — {e}")
            return None

    def _download_assets(self, content, dir_path):
        """
        只下载 HTML 中的图片资源，下载到本地 assets/ 并替换 URL 为相对路径。
        """
        if not content:
            return content

        assets_dir = os.path.join(dir_path, 'assets')
        if not os.path.isdir(assets_dir):
            os.mkdir(assets_dir)

        image_pattern = re.compile(
            r'<img\b[^>]*?\bsrc="([^"]+)"',
            re.IGNORECASE
        )
        urls = image_pattern.findall(content)

        url_map = {}
        for url in urls:
            if url in url_map:
                continue
            filename = self._download_single_url(url, assets_dir)
            if filename:
                url_map[url] = filename

        for remote_url, local_name in url_map.items():
            content = content.replace(remote_url, f'./assets/{local_name}')

        return content

    # ==================== 视频资源下载 ====================

    def _download_video_assets(self, video_info, assets_dir):
        """
        从 API 返回的 video_info 中下载视频文件和封面，返回 <video> 标签 HTML。
        若 video_info 为空或无可用视频 URL，返回空字符串。
        """
        if not video_info:
            return ''

        video_media_map = video_info.get('video_media_map', {})
        video_cover = video_info.get('video_cover', '')
        video_id = video_info.get('video_id', '')
        width = video_info.get('video_width', '')
        height = video_info.get('video_height', '')

        # 选最高清晰度 URL：fhd > hd > sd
        video_url = None
        if video_media_map:
            for quality in ['hd', 'sd', 'ld']:
                if quality in video_media_map:
                    video_url = video_media_map[quality]
                    break
            if not video_url:
                video_url = list(video_media_map.values())[0]

            # video_media_map 的值可能是 dict（含 url/size 等）或纯 string
            if isinstance(video_url, dict):
                log.info(f"  video_media_map 结构: {video_media_map}")
                video_url = video_url.get('url', '') or video_url.get('video_url', '')

        # 检查是否是 HLS 流 (.m3u8)，收集所有 URL 到文件供用户自行下载
        if not video_url or '.m3u8' in str(video_url):
            hls = video_info.get('hls_videos', {}) if not video_url else {}
            all_hls = {}
            if video_media_map:
                all_hls.update(video_media_map)
            if hls:
                all_hls.update(hls)
            if all_hls:
                hls_file = os.path.join(os.path.dirname(assets_dir), 'hls_urls.txt')
                with open(hls_file, 'a', encoding='utf-8') as f:
                    f.write(f'# 文章: {video_id}\n')
                    # 只取 hd 清晰度
                    hd_url = all_hls.get('hd')
                    if hd_url:
                        url_str = hd_url.get('url', hd_url) if isinstance(hd_url, dict) else hd_url
                        f.write(f'{url_str}\n')
                log.info(f"  HD 视频链接已记录到 hls_urls.txt")
            return ''

        # 下载视频文件
        log.info(f"  开始下载视频文件...")
        video_filename = self._download_single_url(video_url, assets_dir)
        if video_filename:
            video_src = f'./assets/{video_filename}'
        else:
            log.warning("  视频文件下载失败，保留远程 URL")
            video_src = video_url

        # 下载视频封面
        poster_path = ''
        if video_cover:
            cover_filename = self._download_single_url(video_cover, assets_dir)
            if cover_filename:
                poster_path = f'./assets/{cover_filename}'

        # 生成 <video> 标签
        dims = ''
        if width:
            dims += f' width="{width}"'
        if height:
            dims += f' height="{height}"'

        return (
            f'<video{dims} controls poster="{poster_path}" '
            f'style="max-width:100%;display:block;margin:16px auto;">\n'
            f'  <source src="{video_src}" type="video/mp4">\n'
            f'</video>\n'
        )

    # ==================== 文件保存 ====================

    def save_to_file(self, dir_name, filename, content, audio=None, file_type=None,
                     comments=None, video_info=None):
        """
        保存文章到独立的 HTML 阅读版目录。若 self.download_assets=True，
        只下载正文图片；音频和视频不写入阅读版。
        """
        if not file_type:
            file_type = '.html'
        source_dir_name = os.fspath(dir_name)
        is_html = file_type.lower() == '.html'
        output_dir_name = source_dir_name
        if is_html and not output_dir_name.endswith(HTML_READING_SUFFIX):
            output_dir_name += HTML_READING_SUFFIX
        dir_path = pathlib.Path(output_dir_name)
        dir_path.mkdir(parents=True, exist_ok=True)
        filename = check_filename(filename)
        file_path = os.path.abspath(dir_path / (filename + file_type))

        # 先生成评论 HTML
        temp = ""
        if comments:
            with open('comment.css', 'r', encoding='utf-8') as f:
                comment_style = f.read()
            temp = comment_style + "<ul>"
            for comment in comments:
                replie_str = ""
                for replie in comment.get('replies', []):
                    replie_str += f"""<p class="_3KxQPN3V_0">{replie['user_name']}: {replie['content']}</p>"""
                comment_str = f"""<li>
<div class="_2sjJGcOH_0"><img src="{comment['user_header']}"
  class="_3FLYR4bF_0">
<div class="_36ChpWj4_0">
  <div class="_2zFoi7sd_0"><span>{comment['user_name']}</span>
  </div>
  <div class="_2_QraFYR_0">{comment['comment_content']}</div>
  <div class="_10o3OAxT_0">
    {replie_str}
  </div>
  <div class="_3klNVc4Z_0">
    <div class="_3Hkula0k_0">{datetime.datetime.fromtimestamp(comment['comment_ctime'])}</div>
  </div>
</div>
</div>
</li>\n"""
                temp += comment_str
            temp += "</ul>"

        # 只下载文章正文图片。阅读版不下载或写入音频、视频。
        if self.download_assets:
            log.info(f"开始下载文章图片资源（文章：{filename}）...")
            content = self._download_assets(content, dir_path)
            temp = self._download_assets(temp, dir_path)
            log.info("文章图片资源下载完成。")

        # 写入带阅读样式的独立 HTML；不插入音频或视频播放器。
        related_html = _build_local_recommendations(
            dir_path, filename + file_type
        ) if file_type.lower() == '.html' else ''
        if is_html:
            author = (
                '邓明'
                if os.path.basename(os.path.normpath(source_dir_name))
                == 'AI Agent 系统设计面试现场'
                else ''
            )
            output_content = _render_reading_page(
                filename, content + temp, related_html, author
            )
        else:
            output_content = content + temp
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(output_content)
        if is_html:
            _write_local_index(dir_path, source_dir_name, author)


def run(cellphone=None, passwd=None, only=None, file_type=None, get_comments=False, download_assets=False, cookie_string=None):
    """ 整体流程的请求方法 """
    global FINISH_ARTICLES
    global ALL_ARTICLES

    geek = GeekCrawler(cellphone, passwd, only=only, download_assets=download_assets, cookie_string=cookie_string)
    geek._login()
    geek._product()

    number = 0
    for pro in geek.products:
        geek._articles(pro['id'], pro)

        article_ids = pro['article_ids']
        for aid in article_ids:

            if set(ALL_ARTICLES) == set(FINISH_ARTICLES):
                import sys
                log.info("正常抓取完成啦，不用再继续跑脚本了。")
                sys.exit(1)

            if str(aid) in FINISH_ARTICLES:
                continue
            geek._article(aid, pro, file_type=file_type, get_comments=get_comments)
            time.sleep(5)
            number += 1
            if number == 37:
                log.info("抓取达到37次了，先暂停 10s 再继续。")
                time.sleep(10)
                number = 0
                # geek._user_auth()
    _save_finish_article_id_to_file()
    log.info("正常抓取完成。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='下载极客时间专栏并生成离线 HTML 阅读版。'
    )
    course_group = parser.add_mutually_exclusive_group()
    course_group.add_argument(
        '--course', action='append', dest='courses', metavar='专栏名称',
        help='指定要下载的专栏；可重复传入多个专栏名称。'
    )
    course_group.add_argument(
        '--all-courses', action='store_true',
        help='下载账号下所有可见专栏。默认只下载 AI Agent 系统设计面试现场。'
    )
    parser.add_argument(
        '--no-images', action='store_true',
        help='不下载文章图片，HTML 中保留原图链接。'
    )
    parser.add_argument(
        '--with-comments', action='store_true',
        help='同时保存评论；默认只保存文章正文和图片。'
    )
    parser.add_argument(
        '--password-login', action='store_true',
        help='忽略 GEEKTIME_COOKIE，使用环境变量或交互式手机号和密码登录。'
    )
    parser.add_argument(
        '--force', action='store_true',
        help='忽略已完成记录，重新下载所选专栏的全部文章。'
    )
    args = parser.parse_args()

    # 凭据只从当前进程环境或交互输入读取，不在源码中保存。
    cookie_string = '' if args.password_login else os.environ.get('GEEKTIME_COOKIE', '').strip()
    cellphone = os.environ.get('GEEKTIME_CELLPHONE') or None
    pwd = os.environ.get('GEEKTIME_PASSWORD') or None

    # 默认只下载当前专栏；也可用 --course 或 --all-courses 选择下载范围。
    only = args.courses or ([] if args.all_courses else list(DEFAULT_COURSES))

    # 文件类型，推荐 .html（多媒体渲染好）
    file_type = '.html'

    # 是否抓取评论
    get_comments = args.with_comments

    # 是否将文章图片保存到阅读版的 assets/ 目录；音视频不进入阅读版。
    download_assets = not args.no_images

    try:
        FINISH_ARTICLES = [] if args.force else _load_finish_article()
        run(cellphone, pwd, only=only, file_type=file_type, get_comments=get_comments,
            download_assets=download_assets,
            cookie_string='' if args.password_login else cookie_string)
    except Exception:
        import traceback
        log.error(f"请求过程中出错了，出错信息为：{traceback.format_exc()}")
    finally:
        _save_finish_article_id_to_file()
