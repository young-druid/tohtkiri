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
from urllib import quote_plus, unquote_plus


class BlogException(Exception):
    def __init__(self, message, code=500):
        Exception.__init__(self, message)
        self.code = code


class Blog(object):
    _statuses = {404: '404 Not Found', 200: '200 OK', 303: '303 See Other',
                 400: '400 Bad Request'}

    _tpl_header = Template('<!DOCTYPE html>\n'
                           '<html xmlns="http://www.w3.org/1999/html">\n'
                           '<head lang="en">\n'
                           '\t<meta charset="utf-8"/>\n'
                           '\t<link rel="stylesheet" href="${base}/styles.css" '
                           'type="text/css" media="screen"/>\n'
                           '\t<title>Lipstick blog</title>\n'
                           '</head>\n'
                           '<body>\n'
                           '\t<header>\n'
                           '\t\t<h1>Lipstick is life</h1>\n'
                           '\t\t<section>\n'
                           '\t\t\t<form method="get" action="${base}/search">'
                           '<label for="search">Search</label>'
                           '<span><input name="q" id="search" '
                           'type="text"/></span></form>\n'
                           '\t\t</section>\n'
                           '\t</header>\n'
                           '\t<main>\n')

    _tpl_link = Template('<a href="${link}">${title}</a>')
    _tpl_link_wth_cls = Template('<a href="${link}" class="${cls}">${title}'
                                 '</a>')

    _tpl_entries_begin = '\t\t<section>\n'
    _tpl_entry = Template('\t\t\t<article>\n'
                          '\t\t\t<header>\n'
                          '\t\t\t\t<a href="#">'
                          '<h2>${title}</h2></a></header>\n'
                          '\t\t\t<time>${time}</time>\n'
                          '\t\t\t<p>${post}</p>\n'
                          '\t\t\t<footer>\n'
                          '\t\t\t\t<div>posted in ${categories}</div>\n'
                          '\t\t\t\t<div><a href="#">${comments}</a>'
                          '</div>\n'
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

    def __init__(self):
        self.environ = None
        self.response = None
        self.app_uri = ""
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
        self.file_name_sep = conf.get('file_name_separator', '-')
        try:
            self.items_per_page = int(conf.get('items_per_page', 7))
        except ValueError:
            self.items_per_page = 7
        self.index = self._try_main_index(os.path.join(self.indices_dir,
                                                       'main.index'))
        self.categories = self.list_categories()
        self.archive = self.list_archive()

    def list_entry_files(self):
        re_file_name = re.compile('^.+' + self.file_name_sep +
                                  '(\d{4}-\d{2}-\d{2})\.txt$')
        for file_name in os.listdir(self.entries_dir):
            if os.path.isfile(os.path.join(self.entries_dir, file_name)):
                matched = re_file_name.match(file_name)
                if matched:
                    try:
                        date = datetime.strptime(matched.group(1), '%Y-%m-%d')
                        yield date, file_name
                    except ValueError:
                        continue

    def _serialize_object(self, obj, file_path):
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.indices_dir)
        with closing(os.fdopen(tmp_fd, 'wb')) as tmp_file:
            cPickle.dump(obj, tmp_file, protocol=cPickle.HIGHEST_PROTOCOL)
        if not os.path.exists(file_path):
            os.rename(tmp_path, file_path)
            self._logger.info('an object was serialized into file [%s]',
                              file_path)
        else:
            os.remove(tmp_path)

    def _try_main_index(self, main_index_path):
        if not os.path.exists(main_index_path):
            return self._create_main_index(main_index_path)
        else:
            with open(main_index_path, 'rb') as f:
                return cPickle.load(f)

    def _create_main_index(self, main_index_path):
        entries = list()
        for date, file_name in self.list_entry_files():
            entries.append((date, file_name, self._read_categories(file_name)))
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

    def status(self, code, response):
        self.response(self._statuses[code], [('Content-Type',
                                              'text/plain; charset=%s' %
                                              self._encoding)])
        return response

    @staticmethod
    def parse_page(page):
        try:
            return int(page)
        except ValueError:
            raise BlogException('[page] parameter is not good [%s]' % page,
                                code=404)

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
        date, file_name, cats = entry
        post = dict()
        post['date'] = date
        with open(os.path.join(self.entries_dir, file_name)) as f:
            preview, full = False, False
            for line in f:
                if line.startswith('categories:') and not 'categories' in post:
                    post['categories'] = [category.strip() for category in
                                          line.lstrip('categories:').split(",")]
                    preview, full = False, False
                elif line.startswith('title:') and not 'title' in post:
                    post['title'] = line.lstrip('title:').strip()
                    preview, full = False, False
                elif line.startswith('preview:') and not preview \
                        and not 'preview' in post:
                    preview, full = True, False
                    post['preview'] = line.lstrip('preview:').lstrip()
                elif line.startswith('full:') and not full \
                        and not 'full' in post:
                    preview, full = False, True
                    post['full'] = line.lstrip('full:').lstrip()
                elif preview:
                    post['preview'] += line
                elif full:
                    post['full'] += line
        if not 'categories' in post:
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

    def build_base_uri(self, category, archive, page):
        uri = self.app_uri
        if category:
            uri += '/category/' + category
        elif archive:
            uri += '/archive/' + archive
        if page:
            uri += '/page/' + str(page)
        return uri

    def configure(self, environ, start_response):
        self.environ = environ
        self.response = start_response
        self.app_uri = wsgiref.util.application_uri(environ)
        main_index_path = os.path.join(self.indices_dir, 'main.index')
        if not os.path.exists(main_index_path):
            self.index = self._try_main_index(main_index_path)
            self.categories = self.list_categories()
            self.archive = self.list_archive()

    def get_list(self, category=None, archive=None, page=1):
        if page > 0:
            if category and not category in self.categories:
                yield self.status(404, 'Category %s not found' % category)
            elif archive and not archive in self.archive:
                yield self.status(404, 'Archive %s not found' % archive)
            else:
                self.response(self._statuses[200], [('Content-Type',
                                                     'text/html; charset=%s' %
                                                     self._encoding)])
                yield self._tpl_header.safe_substitute(base=self.app_uri)
                yield self._tpl_entries_begin
                entries = self.filter_entries(category, archive)
                items_to = self.items_per_page * page
                for entry in entries[items_to - self.items_per_page:items_to]:
                    post = self.read_post(entry)
                    fmt_categories = ", ".join(
                        [self._tpl_link.safe_substitute(link=self.app_uri +
                                                        '/category/' +
                                                        quote_plus(cat),
                                                        title=cat)
                         for cat in post['categories']])
                    if 'preview' in post:
                        post_text = post['preview']
                    elif 'full' in post:
                        post_text = post['full']
                    else:
                        post_text = ''
                    yield self._tpl_entry.safe_substitute(title=post['title'],
                                                          categories=
                                                          fmt_categories,
                                                          time=post['date'].
                                                          strftime('%Y/%m/%d'),
                                                          post=post_text,
                                                          comments=
                                                          'No comments')
                yield self._tpl_entries_end
                fmt_categories = "".join(
                    [self._tpl_aside_entry.safe_substitute(link=self.app_uri +
                                                           '/category/' +
                                                           quote_plus(cat),
                                                           title=cat)
                     for cat in self.categories])
                fmt_archive = "".join(
                    [self._tpl_aside_entry.safe_substitute(link=self.app_uri +
                                                           '/archive/' +
                                                           quote_plus(arc),
                                                           title=arc)
                     for arc in self.archive])
                yield self._tpl_aside.safe_substitute(categories=fmt_categories,
                                                      archive=fmt_archive)
                older_newer = ''
                if entries:
                    if items_to < len(entries):
                        older_newer = self.\
                            _tpl_link_wth_cls.\
                            substitute(link=self.build_base_uri(category,
                                                                archive,
                                                                page + 1),
                                       cls='older',
                                       title='&#9668;&nbsp;Older')
                    if page > 1 and items_to - self.items_per_page < \
                            len(entries):
                        older_newer += self.\
                            _tpl_link_wth_cls.\
                            substitute(link=self.build_base_uri(category,
                                                                archive,
                                                                page - 1),
                                       cls='newer',
                                       title='Newer&nbsp;&#9658;')
                yield self._tpl_footer.safe_substitute(links=older_newer)
        else:
            yield self.status(404, 'Page %d not found' % page)

    def __call__(self, environ, start_response):
        try:
            self.configure(environ, start_response)
            path = self.environ.get('PATH_INFO', '/')
            method = self.environ['REQUEST_METHOD'].upper()
            if method == 'GET':
                if not path or path == '/':
                    return self.get_list()
                elif re.match('^/page/\d+/?$', path):
                    return self.get_list(page=
                                         self.parse_page(path.split('/')[2]))
                elif re.match('^/category/[^/]+/?$', path):
                    return self.get_list(category=
                                         unquote_plus(path.split('/')[2]))
                elif re.match('^/category/[^/]+/page/\d+/?$', path):
                    path_els = path.split('/')
                    return self.get_list(category=unquote_plus(path_els[2]),
                                         page=self.parse_page(path_els[4]))
                elif re.match('^/archive/\d{4}-\d{2}/?$', path):
                    return self.get_list(archive=path.split('/')[2])
                elif re.match('^/archive/\d{4}-\d{2}/page/\d+/?$', path):
                    path_els = path.split('/')
                    return self.get_list(archive=path_els[2], page=self.
                                         parse_page(path_els[4]))
            elif method == 'POST':
                return self.status(404, 'Page %s not found' % path)
            return self.status(404, 'Page %s not found' % path)
        except BlogException as e:
            return self.status(e.code, e.message)

application = Blog()