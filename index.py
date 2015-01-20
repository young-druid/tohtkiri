#!/usr/bin/env python
# -*- coding: UTF-8 -*-
import logging
import os
import re
from datetime import datetime
import cPickle
import tempfile
from contextlib import closing
from string import Template
import wsgiref.util
import cgi
from urllib import quote_plus, unquote_plus
import hashlib
import random


class RequestContext(object):
    def __init__(self, environ, start_response):
        self.environ = environ
        self.response = start_response
        app_uri = wsgiref.util.application_uri(environ)
        self.app_uri = app_uri if not app_uri.endswith('/') \
            else app_uri[0:len(app_uri) - 1]
        self.path = self.environ.get('PATH_INFO', '/')
        self.method = self.environ['REQUEST_METHOD'].upper()


class Blog(object):
    _statuses = {404: '404 Not Found', 200: '200 OK', 303: '303 See Other',
                 400: '400 Bad Request'}

    _tpl_header = Template('<!DOCTYPE html>\n'
                           '<html xmlns="http://www.w3.org/1999/html">\n'
                           '<head lang="en">\n'
                           '\t<meta charset="${encoding}"/>\n'
                           '\t<link rel="stylesheet" href="${base}/styles.css" '
                           'type="text/css" media="screen"/>\n'
                           '\t<script type="text/javascript" '
                           'src="${base}/script.js"></script>\n'
                           '\t<link type="application/atom+xml" rel="alternate"'
                           ' title="${title}" href="${feed_url}" />\n'
                           '\t<title>${title}</title>\n'
                           '</head>\n'
                           '<body${body_tag}>\n'
                           '\t<header>\n'
                           '\t\t<h1>${title}</h1>\n'
                           '\t</header>\n'
                           '\t<main>\n')

    _tpl_link = Template('<a href="${link}">${title}</a>')
    _tpl_link_wth_cls = Template('<a href="${link}" class="${cls}">${title}'
                                 '</a>')

    _tpl_entries_begin = '\t\t<section>\n'
    _tpl_entry = Template('\t\t\t<article>\n'
                          '\t\t\t<header>\n'
                          '\t\t\t\t<h2>${title}</h2></header>\n'
                          '\t\t\t<time>${time}</time>\n'
                          '\t\t\t<p>${text}</p>\n'
                          '\t\t\t<footer>\n'
                          '\t\t\t\t<div>posted in ${categories}</div>\n'
                          '\t\t\t\t<div>${comments}</div>\n'
                          '\t\t\t</footer>\n'
                          '\t\t\t</article>\n')
    _tpl_view_full = Template('<a href="#">View full post &rarr;</a>')
    _tpl_entries_end = '\t\t</section>\n'

    _tpl_aside = Template('\t\t<aside>\n'
                          '\t\t\t<nav><h2>Categories</h2>\n'
                          '\t\t\t\t<ul>\n'
                          '${categories}'
                          '\t\t\t\t</ul>\n'
                          '\t\t\t</nav>\n'
                          '\t\t\t<nav><h2>Archive</h2>\n'
                          '\t\t\t\t<ul>\n'
                          '${archive}'
                          '\t\t\t\t</ul>\n'
                          '\t\t\t</nav>\n'
                          '\t\t</aside>\n')

    _tpl_aside_entry = Template('\t\t\t\t\t<li><a href="${link}">${title}</a>'
                                '</li>\n')

    _tpl_footer = Template('\t</main>\n'
                           '\t<footer>\n'
                           '\t\t<nav>${links}</nav>\n'
                           '\t</footer>\n'
                           '</body>\n'
                           '</html>\n')

    _tpl_post = Template('\t\t\t<article>\n'
                         '\t\t\t\t<header><h2>${title}</h2></header>\n'
                         '\t\t\t\t<time>${time}</time>\n'
                         '\t\t\t\t<p>${text}</p>\n'
                         '\t\t\t\t<footer>\n'
                         '\t\t\t\t<div>posted in ${categories}</div>\n'
                         '\t\t\t\t<div class="comments" id="comments">\n'
                         '\t\t\t\t<header><h3>${comments_title}</h3>'
                         '<a onclick="toggleReplyForm(\'reply-form\');'
                         'return false;" href="#comments">Reply</a></header>\n'
                         '\t\t\t\t<div class="reply-wrapper" id="reply-form" '
                         'style="display:none;">\n'
                         '\t\t\t\t<form method="post" class="reply-form"'
                         'action="${reply_url}">\n'
                         '\t\t\t\t<div><input type="text" class="cobweb" '
                         'name="cobweb" placeholder="Please, paste ${token} '
                         'value into this field" value=""/></div>\n'
                         '\t\t\t\t<div><input name="email" '
                         'type="text" placeholder="Email" value=""/></div>\n'
                         '\t\t\t\t<div><input name="name" '
                         'type="text" placeholder="Name" value=""/></div>\n'
                         '\t\t\t\t<div><textarea rows="4" placeholder="Comment"'
                         ' name="comment"></textarea></div>\n'
                         '\t\t\t\t<div><input type="submit" value="Send"/>'
                         '</div>\n'
                         '\t\t\t\t</form>\n'
                         '\t\t\t\t</div>\n'
                         '\t\t\t\t\t${comments}\n'
                         '\t\t\t\t</div>\n'
                         '\t\t\t\t</footer>\n'
                         '\t\t\t</article>\n')

    _tpl_comment = Template('\t\t\t\t<div class="comment">\n'
                            '\t\t\t\t<div class="comment_body">\n'
                            '\t\t\t\t<header><h3>${name}</h3>'
                            '<time>${time}</time><span class="delete">'
                            '${delete_url}</span></header>\n'
                            '\t\t\t\t<p>${comment}</p>\n'
                            '\t\t\t\t<footer><a href="#" '
                            'onclick="toggleReplyForm(\'reply-form-${id}\');'
                            'return false;">Reply</a></footer>\n'
                            '\t\t\t\t<div class="reply-wrapper" '
                            'id="reply-form-${id}" style="display:none;">\n'
                            '\t\t\t\t<form method="post" class="reply-form"'
                            'action="${reply_url}">\n'
                            '\t\t\t\t<div><input type="text" class="cobweb" '
                            'name="cobweb" placeholder="Please, paste ${token} '
                            'value into this field" value=""/></div>\n'
                            '\t\t\t\t<input name="comment_no" type="hidden" '
                            'value="${id}"/>\n'
                            '\t\t\t\t<div><input name="email" '
                            'type="text" placeholder="Email" value=""/></div>\n'
                            '\t\t\t\t<div><input name="name" '
                            'type="text" placeholder="Name" value=""/></div>\n'
                            '\t\t\t\t<div><textarea rows="4" '
                            'placeholder="Comment" name="comment"></textarea>'
                            '</div>\n'
                            '\t\t\t\t<div><input type="submit" value="Send"/>'
                            '</div>\n'
                            '\t\t\t\t</form>\n'
                            '\t\t\t\t</div>\n'
                            '\t\t\t\t</div>\n'
                            '\t\t\t\t<div class="reply_comments">${comments}'
                            '</div>\n'
                            '\t\t\t\t</div>\n')

    _tpl_delete_comment = Template('\t\t\t<form method="post" '
                                   'action="${url}/${ids}">\n'
                                   '\t\t\t\t<input name="password" '
                                   'type="password" placeholder="Password"/>\n'
                                   '\t\t\t\t<input type="submit" '
                                   'value="Delete"/>\n'
                                   '\t\t\t</form>\n')

    _tpl_feed_begin = Template('<?xml version="1.0" encoding="${encoding}"?>\n'
                               '<feed xmlns="http://www.w3.org/2005/Atom">\n'
                               '\t<title>${title}</title>\n'
                               '\t<link rel="self" type="text/xml" '
                               'href="${self_url}"/>\n'
                               '\t<link type="text/html" rel="alternate" '
                               'href="${url}"/>\n'
                               '\t<updated>${updated}</updated>\n'
                               '\t<author><name>${author}</name></author>\n'
                               '\t<id>urn:${id}</id>\n')

    _tpl_feed_entry = Template('\t<entry>\n'
                               '\t\t<id>urn:${id}</id>\n'
                               '\t\t<title>${title}</title>\n'
                               '\t\t<link type="text/html" rel="alternate" '
                               'href="${url}"/>\n'
                               '${categories}'
                               '\t\t<updated>${updated}</updated>\n'
                               '\t\t<content type="text/html">${content}'
                               '</content>\n'
                               '\t</entry>\n')

    _tpl_feed_category = Template('\t\t<category term="${category}"/>\n')

    _tpl_feed_end = '</feed>'

    def __init__(self):
        self._encoding = 'UTF-8'
        script_path, _ = os.path.split(os.path.realpath(__file__))
        conf = dict()
        conf_path = os.path.join(script_path, 'index.conf')
        try:
            execfile(conf_path, conf)
        except IOError:
            print 'I wasn\'t able to read configuration file [%s]. Default ' \
                  'settings will be used' % conf_path
        logging.basicConfig(level=logging.DEBUG)
        self._logger = logging.getLogger(__name__)
        self.entries_dir = conf.get('entries_path', os.path.join(script_path,
                                                                 'entries'))
        self.indices_dir = conf.get('indices_path', os.path.join(script_path,
                                                                 'indices'))
        self.comments_dir = conf.get('comments_path', os.path.join(script_path,
                                                                   'comments'))
        self.file_name_sep = conf.get('file_name_separator', '-')
        self.title = conf.get('title', '')
        try:
            self.items_per_page = int(conf.get('items_per_page', 7))
        except ValueError:
            self.items_per_page = 7
        try:
            self.items_per_feed = int(conf.get('items_per_feed', 7))
        except ValueError:
            self.items_per_feed = 7
        try:
            self.comments_nesting = int(conf.get('comments_nesting', 7))
        except ValueError:
            self.comments_nesting = 7
        self.index = self._try_main_index(os.path.join(self.indices_dir,
                                                       'main.index'))
        self.author = conf.get('author', 'anonymous')
        self.categories = self.list_categories()
        self.archive = self.list_archive()
        password = conf.get('password')
        if password:
            m = hashlib.md5()
            m.update(password)
            self.password = m.digest()
        else:
            self.password = None
        self._salt = conf.get('salt', ''.join(random.choice('0123456789ABCDEF')
                                              for _ in range(16)))

    def _serialize_object(self, obj, file_path, force=False):
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.indices_dir)
        with closing(os.fdopen(tmp_fd, 'wb')) as tmp_file:
            cPickle.dump(obj, tmp_file, protocol=cPickle.HIGHEST_PROTOCOL)
        try:
            if not os.path.exists(file_path) or force:
                os.rename(tmp_path, file_path)
                self._logger.debug('an object was serialized into file [%s]',
                                   file_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def _try_main_index(self, main_index_path):
        if not os.path.exists(main_index_path):
            return self._create_main_index(main_index_path)
        else:
            with open(main_index_path, 'rb') as f:
                return cPickle.load(f)

    def _create_main_index(self, main_index_path):
        entries = list()
        re_file_name = re.compile('^(.+)' + self.file_name_sep +
                                  '(\d{4}-\d{2}-\d{2})\.txt$')
        for file_name in os.listdir(self.entries_dir):
            if os.path.isfile(os.path.join(self.entries_dir, file_name)):
                matched = re_file_name.match(file_name)
                if matched:
                    try:
                        date = datetime.strptime(matched.group(2), '%Y-%m-%d')
                        entries.append((date, matched.group(1),
                                        self._read_categories(file_name)))
                    except ValueError:
                        continue
        entries.sort(reverse=True, key=lambda entry: (entry[0], entry[1]))
        self._serialize_object(entries, main_index_path)
        return entries

    def _read_categories(self, file_name):
        re_categories = re.compile('^categories:([^,]+)(?:,([^,]+))*$')
        categories = set()
        with open(os.path.join(self.entries_dir, file_name), mode='r') as f:
            for line in f:
                matched = re_categories.match(line)
                if matched:
                    for category in matched.groups():
                        categories.add(category.strip())
                    break
        return categories

    def status(self, rc, code, response):
        rc.response(self._statuses[code], [('Content-Type',
                                            'text/plain; charset=%s' %
                                            self._encoding)])
        return response

    def redirect(self, rc, url):
        rc.response(self._statuses[303], [('Location', rc.app_uri + url)])

    def list_file_names(self):
        main_index_path = os.path.join(self.indices_dir, 'main.index')
        if not os.path.exists(main_index_path):
            self._create_main_index(main_index_path)
        with open(main_index_path, 'rb') as f:
            return cPickle.load(f)

    def filter_entries(self, category, archive):
        if category:
            return [entry for entry in self.index if category in entry[2]]
        elif archive:
            archive = datetime.strptime(archive, '%Y-%m')
            return [entry for entry in self.index if (archive.year,
                                                      archive.month) ==
                                                     (entry[0].year,
                                                      entry[0].month)]
        return self.index

    def read_post(self, entry):
        date, pid, cats = entry
        post = dict()
        post['date'] = date
        post['id'] = pid
        with open(os.path.join(self.entries_dir,
                               self.build_file_name(entry))) as f:
            preview, full = False, False
            for line in f:
                if line.startswith('categories:') and 'categories' not in post:
                    post['categories'] = [category.strip() for category in
                                          line.lstrip('categories:').split(",")]
                    preview, full = False, False
                elif line.startswith('title:') and 'title' not in post:
                    post['title'] = line.lstrip('title:').strip()
                    preview, full = False, False
                elif line.startswith('preview:') and not preview \
                        and 'preview' not in post:
                    preview, full = True, False
                    post['preview'] = line.lstrip('preview:').lstrip()
                elif line.startswith('full:') and not full \
                        and 'full' not in post:
                    preview, full = False, True
                    post['full'] = line.lstrip('full:').lstrip()
                elif preview:
                    post['preview'] += line
                elif full:
                    post['full'] += line
        if 'categories' not in post:
            post['categories'] = []
        return post

    def list_archive(self):
        archive = list()
        year, month = None, None
        for date, _, _ in self.index:
            if (date.year, date.month) != (year, month):
                archive.append(str(date.year) + "-" + str(date.month))
                year, month = date.year, date.month
        return archive

    def list_categories(self):
        categories = set()
        for _, _, cats in self.index:
            categories.update(cats)
        categories = list(categories)
        categories.sort()
        return categories

    @staticmethod
    def build_base_uri(app_uri, category, archive, page):
        uri = app_uri
        if category:
            uri += '/category/' + category
        elif archive:
            uri += '/archive/' + archive
        if page:
            uri += '/page/' + str(page)
        return uri

    def build_file_name(self, entry):
        date, pid, _ = entry
        return pid + self.file_name_sep + date.strftime('%Y-%m-%d') + '.txt'

    def find_entry(self, archive, pid):
        try:
            date = datetime.strptime(archive, '%Y-%m-%d')
            entry = next(entry for entry in self.index
                         if (date, pid) == entry[0:2])
            return entry if entry else None
        except ValueError:
            return None

    def get_comment(self, comments, comments_num):
        comment = None
        level = 0
        while comments_num:
            index = comments_num[0]
            if level == self.comments_nesting:
                return comment
            if index < len(comments):
                comment = comments[index]
                comments, comments_num = comment[4], comments_num[1:]
            else:
                comment, comments_num = None, None
            level += 1
        return comment

    def gather_comments(self, app_uri, comments, archive, pid, token, admin):
        reply_url = app_uri + '/post/' + archive + '/' + pid
        delete_url = app_uri + '/delete/' + archive + '/' + pid

        def _gather_comments(_comments, _buf, _count, ids):
            for idx, comment in enumerate(_comments):
                date, _, name, text, replies = comment
                _ids = list(ids)
                _ids.append(str(idx))
                comments_str, comments_count = \
                    _gather_comments(replies, [], 0, _ids)
                ids_str = "-".join(_ids)
                _buf.append(self._tpl_comment.
                            substitute(name=cgi.escape(name) or 'anonymous',
                                       time=date.strftime('%Y/%m/%d @ %H:%M'),
                                       reply_url=reply_url, id=ids_str,
                                       comment=cgi.escape(text),
                                       delete_url=self._tpl_link.
                                       substitute(link=delete_url + '/' +
                                                  ids_str,
                                                  title='X') if admin else '',
                                       comments=comments_str, token=token))
                _count += comments_count + 1
            return "".join(_buf), _count
        buf, count = _gather_comments(comments, [], 0, [])
        return "".join(buf), count

    def load_comments(self, archive, pid):
        comments_path = os.path.join(self.comments_dir,
                                     pid + self.file_name_sep + archive +
                                     '.comments')
        if os.path.exists(comments_path):
            with open(comments_path, 'rb') as f:
                return cPickle.load(f)
        else:
            return list()

    def count_comments(self, comments):
        if comments:
            count = len(comments)
            for _, _, _, _, replies in comments:
                count += self.count_comments(replies)
            return count
        return 0

    def configure(self):
        main_index_path = os.path.join(self.indices_dir, 'main.index')
        if not os.path.exists(main_index_path):
            self.index = self._try_main_index(main_index_path)
            self.categories = self.list_categories()
            self.archive = self.list_archive()

    def get_list(self, rc, category=None, archive=None, page=1):
        if page > 0:
            if category and category not in self.categories:
                yield self.status(rc, 404, 'Category %s not found' % category)
            elif archive and archive not in self.archive:
                yield self.status(rc, 404, 'Archive %s not found' % archive)
            else:
                rc.response(self._statuses[200], [('Content-Type',
                                                   'text/html; charset=%s' %
                                                   self._encoding)])
                yield self._tpl_header.\
                    substitute(base=rc.app_uri, feed_url=rc.app_uri + '/rss'
                               + ('/' + category if category else ''),
                               title=cgi.escape(self.title, quote=True),
                               encoding=self._encoding.lower(), body_tag='')
                yield self._tpl_entries_begin
                entries = self.filter_entries(category, archive)
                items_to = self.items_per_page * page
                for entry in entries[items_to - self.items_per_page:items_to]:
                    post = self.read_post(entry)
                    date_for_link = post['date'].strftime('%Y-%m-%d')
                    fmt_categories = ", ".join(
                        [self._tpl_link.substitute(link=rc.app_uri +
                                                   '/category/' +
                                                   quote_plus(cat), title=cat)
                         for cat in post['categories']])
                    if 'preview' in post:
                        post_text = post['preview']
                        if 'full' in post:
                            post_text += self._tpl_link.substitute(
                                link=rc.app_uri + '/post/' + date_for_link +
                                '/' + post['id'], title='View full post &rarr;')
                    elif 'full' in post:
                        post_text = post['full']
                    else:
                        post_text = ''
                    title = self.\
                        _tpl_link.substitute(link=rc.app_uri + '/post/' +
                                             date_for_link + '/' + post['id'],
                                             title=post['title'])
                    comments_count = self.\
                        count_comments(self.load_comments(date_for_link,
                                                          post['id']))
                    comments_str = 'No comments'
                    if comments_count == 1:
                        comments_str = '1 comment'
                    elif comments_count > 1:
                        comments_str = '%d comments' % comments_count
                    comments_str = self._tpl_link.\
                        substitute(link=rc.app_uri + '/post/' +
                                   date_for_link + '/' + post['id'] +
                                   '#comments', title=comments_str)
                    yield self._tpl_entry.\
                        substitute(title=title, categories=fmt_categories,
                                   time=post['date'].strftime('%Y/%m/%d'),
                                   text=post_text, comments=comments_str)
                yield self._tpl_entries_end
                fmt_categories = "".join(
                    [self._tpl_aside_entry.substitute(link=rc.app_uri +
                                                      '/category/' +
                                                      quote_plus(cat),
                                                      title=cat)
                     for cat in self.categories])
                fmt_archive = "".join(
                    [self._tpl_aside_entry.substitute(link=rc.app_uri +
                                                      '/archive/' +
                                                      quote_plus(arc),
                                                      title=arc)
                     for arc in self.archive])
                yield self._tpl_aside.substitute(categories=fmt_categories,
                                                 archive=fmt_archive)
                older_newer = ''
                if entries:
                    if items_to < len(entries):
                        older_newer = self.\
                            _tpl_link_wth_cls.\
                            substitute(link=Blog.build_base_uri(rc.app_uri,
                                                                category,
                                                                archive,
                                                                page + 1),
                                       cls='older',
                                       title='&#9668;&nbsp;Older')
                    if page > 1 and items_to - self.items_per_page < \
                            len(entries):
                        older_newer += self.\
                            _tpl_link_wth_cls.\
                            substitute(link=Blog.build_base_uri(rc.app_uri,
                                                                category,
                                                                archive,
                                                                page - 1),
                                       cls='newer',
                                       title='Newer&nbsp;&#9658;')
                yield self._tpl_footer.substitute(links=older_newer)
        else:
            yield self.status(rc, 404, 'Page %d not found' % page)

    def get_post(self, rc, archive, pid, admin=False):
        entry = self.find_entry(archive, pid)
        if entry:
            post = self.read_post(entry)
            rc.response(self._statuses[200], [('Content-Type',
                                               'text/html; charset=%s' %
                                               self._encoding)])
            m = hashlib.sha1()
            m.update(archive + pid + self._salt)
            token = m.hexdigest()
            yield self._tpl_header.\
                substitute(base=rc.app_uri, encoding=self._encoding,
                           feed_url=rc.app_uri + '/rss',
                           body_tag=' onload="setToken(\'' + token + '\');"',
                           title=cgi.escape(self.title, quote=True))
            yield self._tpl_entries_begin
            fmt_categories = ", ".join(
                [self._tpl_link.substitute(link=rc.app_uri + '/category/' +
                                           quote_plus(cat), title=cat)
                 for cat in post['categories']])
            if 'full' in post:
                post_text = post['full']
            elif 'preview' in post:
                post_text = post['preview']
            else:
                post_text = ''
            comments_path = os.path.join(self.comments_dir, pid +
                                         self.file_name_sep + archive +
                                         '.comments')
            comments_title = 'No comments'
            comments_str = ''
            if os.path.exists(comments_path):
                with open(comments_path, 'rb') as f:
                    comments = cPickle.load(f)
                comments_str, count = self.gather_comments(rc.app_uri, comments,
                                                           archive, pid, token,
                                                           admin)
                if count == 1:
                    comments_title = '1 comment'
                elif count > 1:
                    comments_title = '%d comments' % count
            yield self._tpl_post.\
                substitute(title=post['title'], categories=fmt_categories,
                           time=post['date'].strftime('%Y/%m/%d'),
                           text=post_text, comments_title=comments_title,
                           comments=comments_str, reply_url=rc.app_uri +
                           '/post/' + archive + '/' + pid, token=token)
            yield self._tpl_entries_end
            fmt_categories = "".join(
                [self._tpl_aside_entry.substitute(link=rc.app_uri +
                                                  '/category/' +
                                                  quote_plus(cat), title=cat)
                 for cat in self.categories])
            fmt_archive = "".join(
                [self._tpl_aside_entry.substitute(link=rc.app_uri +
                                                  '/archive/' + quote_plus(arc),
                                                  title=arc)
                 for arc in self.archive])
            yield self._tpl_aside.substitute(categories=fmt_categories,
                                             archive=fmt_archive)
        else:
            yield self.status(rc, 404, 'Post %s not found' % archive + '/' +
                                       pid)

    def get_delete_comment(self, rc, archive, pid, ids_str):
        entry = self.find_entry(archive, pid)
        if entry:
            rc.response(self._statuses[200], [('Content-Type',
                                               'text/html; charset=%s' %
                                               self._encoding)])
            yield self._tpl_header.\
                substitute(base=rc.app_uri, encoding=self._encoding,
                           feed_url=rc.app_uri + '/rss', body_tag='',
                           title=cgi.escape(self.title, quote=True))
            yield self._tpl_entries_begin
            yield self.\
                _tpl_delete_comment.\
                substitute(url=rc.app_uri + '/delete/' + archive + '/' + pid,
                           ids=ids_str)
            yield self._tpl_entries_end
            fmt_categories = "".join(
                [self._tpl_aside_entry.substitute(link=rc.app_uri +
                                                  '/category/' +
                                                  quote_plus(cat), title=cat)
                 for cat in self.categories])
            fmt_archive = "".join(
                [self._tpl_aside_entry.substitute(link=rc.app_uri +
                                                  '/archive/' + quote_plus(arc),
                                                  title=arc)
                 for arc in self.archive])
            yield self._tpl_aside.substitute(categories=fmt_categories,
                                             archive=fmt_archive)
        else:
            yield self.status(rc, 404, 'Post %s not found' % archive + '/' +
                                       pid)

    def get_rss(self, rc, category=None):
        if category and category not in self.categories:
            yield self.status(rc, 404, 'Category %s not found' % category)
        else:
            rc.response(self._statuses[200], [('Content-Type',
                                               'application/atom+xml; '
                                               'charset=%s' %
                                               self._encoding)])
            datetime_format = '%Y-%m-%dT%H-%M-%SZ'
            entries = self.filter_entries(category, None)
            updated = datetime(1986, 4, 26)
            if entries:
                updated = entries[0][0].strftime(datetime_format)
            yield self._tpl_feed_begin.\
                substitute(encoding=self._encoding.lower(),
                           self_url=rc.app_uri + '/rss' +
                           ('/' + category if category else ''),
                           title=self.title,
                           author=self.author, url=rc.app_uri +
                           ('/category/' + category if category else ''),
                           id=rc.app_uri + ('/category/' +
                           category if category else ''), updated=updated)
            for entry in entries[:self.items_per_feed]:
                post = self.read_post(entry)
                date_for_link = post['date'].strftime('%Y-%m-%d')
                post_text = ''
                if 'preview' in post:
                    post_text = post['preview']
                elif 'full' in post:
                    post_text = post['full']

                fmt_categories = "".join(
                    [self._tpl_feed_category.substitute(category=cat)
                     for cat in post['categories']])
                yield self.\
                    _tpl_feed_entry.\
                    substitute(id=date_for_link + ':' + post['id'],
                               title=post['title'], url=rc.app_uri + '/post/'
                               + date_for_link + '/' + post['id'],
                               updated=post['date'].strftime(datetime_format),
                               categories=fmt_categories, content=post_text)
            yield self._tpl_feed_end

    def post_comment(self, rc, archive, pid):
        entry = self.find_entry(archive, pid)
        if entry:
            fs = cgi.FieldStorage(keep_blank_values=1,
                                  fp=rc.environ['wsgi.input'],
                                  environ=rc.environ)
            email = fs.getvalue('email', '')
            name = fs.getvalue('name', '')
            comment = fs.getvalue('comment', '')
            comments_no_str = fs.getvalue('comment_no')
            cobweb = fs.getvalue('cobweb', '')
            m = hashlib.sha1()
            m.update(archive + pid + self._salt)
            if m.hexdigest() == cobweb:
                try:
                    comments_no = [int(comment_no) for comment_no
                                   in comments_no_str.split("-")] if \
                        comments_no_str else []
                    path_comment_file = os.path.\
                        join(self.comments_dir, pid + self.file_name_sep +
                             archive + '.comments')
                    comments = list()
                    if os.path.exists(path_comment_file):
                        with open(path_comment_file, 'rb') as f:
                            comments = cPickle.load(f)
                    parent_comment = self.get_comment(comments, comments_no)
                    replies = parent_comment[4] if parent_comment else comments
                    replies.append((datetime.now(), email, name, comment, []))
                    replies.sort(key=lambda c: c[0], reverse=True)
                    self._serialize_object(comments, path_comment_file,
                                           force=True)
                    self.redirect(rc, '/post/' + archive + '/' + pid)
                except ValueError:
                    yield self.status(rc, 401, 'I cannot understand comment_no '
                                               '[%s] parameter' %
                                               comments_no_str)
                except IOError:
                    self._logger.error("IOError occurred while adding comment",
                                       exc_info=1)
                    self.redirect(rc, '/post/' + archive + '/' + pid)
            else:
                yield self.status(rc, 400, 'Token for preventing spam was not '
                                           'valid during comment submission. '
                                           'Please, try again')
        else:
            yield self.status(rc, 404, 'Post %s not found' % archive + '/' +
                                       pid)

    def post_delete_comment(self, rc, archive, pid, ids_str):
        entry = self.find_entry(archive, pid)
        if entry:
            fs = cgi.FieldStorage(keep_blank_values=1,
                                  fp=rc.environ['wsgi.input'],
                                  environ=rc.environ)
            password = fs.getvalue('password', '')
            m = hashlib.md5()
            m.update(password)
            if m.digest() == self.password:
                try:
                    ids = [int(id_str) for id_str
                           in ids_str.split("-")] if ids_str else []
                    if not ids:
                        raise ValueError()
                    path_comment_file = os.path.\
                        join(self.comments_dir, pid + self.file_name_sep +
                             archive + '.comments')
                    if os.path.exists(path_comment_file):
                        with open(path_comment_file, 'rb') as f:
                            comments = cPickle.load(f)
                        id_to_delete = ids.pop()
                        parent_comment = self.get_comment(comments, ids)
                        replies = comments if not ids else \
                            (parent_comment[4] if parent_comment else [])
                        if id_to_delete < len(replies):
                            del replies[id_to_delete]
                            self._serialize_object(comments, path_comment_file,
                                                   force=True)
                        else:
                            self._logger.warn('Comment was not deleted. '
                                              'comment_no is [%s]', ids_str)
                    self.redirect(rc, '/post/' + archive + '/' + pid)
                except ValueError:
                    yield self.status(rc, 400, 'I cannot understand ids [%s] '
                                               'parameter' % ids_str)
            else:
                self._logger.warn('Wrong password was provided in order '
                                  'to delete comment %s/%s/%s', archive, pid,
                                  ids_str)
                self.redirect(rc, '/post/' + archive + '/' + pid)
        else:
            yield self.status(rc, 404, 'Post %s not found' % archive + '/' +
                                       pid)

    def __call__(self, environ, start_response):
        self.configure()
        rc = RequestContext(environ, start_response)
        if rc.method == 'GET':
            if not rc.path or rc.path == '/':
                return self.get_list(rc)
            elif re.match('^/page/\d+/?$', rc.path):
                return self.get_list(rc, page=int(rc.path.split('/')[2]))
            elif re.match('^/category/[^/]+/?$', rc.path):
                return self.get_list(rc, category=unquote_plus(
                    rc.path.split('/')[2]))
            elif re.match('^/category/[^/]+/page/\d+/?$', rc.path):
                path_els = rc.path.split('/')
                return self.get_list(rc, category=unquote_plus(path_els[2]),
                                     page=int(path_els[4]))
            elif re.match('^/archive/\d{4}-\d{2}/?$', rc.path):
                return self.get_list(rc, archive=rc.path.split('/')[2])
            elif re.match('^/archive/\d{4}-\d{2}/page/\d+/?$', rc.path):
                path_els = rc.path.split('/')
                return self.get_list(rc, archive=path_els[2],
                                     page=int(path_els[4]))
            elif re.match('^/post/\d{4}-\d{2}-\d{2}/[^/]+/?$', rc.path):
                path_els = rc.path.split('/')
                return self.get_post(rc, archive=path_els[2],
                                     pid=unquote_plus(path_els[3]))
            elif re.match('^/post/\d{4}-\d{2}-\d{2}/[^/]+/admin/?$', rc.path):
                path_els = rc.path.split('/')
                return self.get_post(rc, archive=path_els[2],
                                     pid=unquote_plus(path_els[3]),
                                     admin=True)
            elif re.match('^/delete/\d{4}-\d{2}-\d{2}/[^/]+/\d+(-\d+)*/?$',
                          rc.path):
                path_els = rc.path.split('/')
                return self.get_delete_comment(rc, archive=path_els[2],
                                               pid=unquote_plus(path_els[3]),
                                               ids_str=path_els[4])
            elif re.match('^/rss/?$', rc.path):
                return self.get_rss(rc)
            elif re.match('^/rss/[^/]+/?$', rc.path):
                return self.get_rss(rc.path.split('/')[2])
        elif rc.method == 'POST':
            if re.match('^/post/\d{4}-\d{2}-\d{2}/[^/]+/?$', rc.path):
                path_els = rc.path.split('/')
                return self.post_comment(rc, archive=path_els[2],
                                         pid=unquote_plus(path_els[3]))
            elif re.match('^/delete/\d{4}-\d{2}-\d{2}/[^/]+/\d+(-\d+)*/?$',
                          rc.path):
                path_els = rc.path.split('/')
                return self.post_delete_comment(rc, path_els[2],
                                                unquote_plus(path_els[3]),
                                                path_els[4])
        return self.status(rc, 404, 'Page %s not found' % rc.path)

application = Blog()