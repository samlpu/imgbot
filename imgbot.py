import functools
import io
import json
import multiprocessing
import os
import zipfile
from urllib.parse import urlparse

import praw
import requests
from bs4 import BeautifulSoup


# use session so TCP connections are reused across bots
session = requests.Session()
# these extensions will be recognized as a direct images
IMAGE_FORMATS = ('.png', '.gif', '.gifv', '.jpg', '.jpeg')
# selectors.json contains tag/attribute identifiers for image links
IMAGE_SELECTORS = {
    "default": {"name": "meta", "property": "og:image", "link": "content"},
    "imgur.com": {"name": "link", "rel": "image_src", "link": "href"},
    "tinypic.com": {"name": "a", "class": "thickbox", "link": "href"},
	"gfycat.com": {"name": "meta", "property": "og:url", "link": "content"}
}

if os.path.isfile('selectors.json'):
    try:
        with open('selectors.json') as f:
            selectors = json.load(f)
            # merge user-defined image selectors
            IMAGE_SELECTORS = {**IMAGE_SELECTORS, **selectors}
    except:
        print('JSON could not read the file. Please check formatting.')


def get_request(url):
    """Checks for bad responses and returns request object."""
    # some website URL schemes do not have the protocol included
    if not url.startswith(('http://', 'https://')):
        url = f'http://{url}'
    req = session.get(url)
    if not req.ok:
        print(f'Encountered bad url: {url}')
        return None
    return req


def get_image_url(url):
    """Returns direct image url from supported page."""
    # get domain name from url: http://imgur.com/ASoeL -> imgur.com
    domain = urlparse(url).netloc
    try:
        # copy the dict because we pop from it
        selectors = IMAGE_SELECTORS[domain].copy()
    except KeyError:
        # default selectors seems to be a common pattern among websites
        selectors = IMAGE_SELECTORS["default"].copy()

    # attribute containing the image link, pop it from dict
    # so we can easily unpack the other keys to the find method
    link = selectors.pop('link')
    req = get_request(url)
    if req is None:
        return None
    soup = BeautifulSoup(req.text, 'html.parser')
    # unpack selectors into keyword arguments
    img = soup.find(**selectors)

    try:
        return img.get(link)
    except AttributeError:
        print(f'[-] Encountered unsupported URL: {url} with domain {domain}')
        return None


def download_image(req, path):
    """Downloads image to the specified download path."""
    filename = os.path.basename(req.url)
    with open(os.path.join(path, filename), 'wb') as file:
        for chunk in req.iter_content(512):
            file.write(chunk)


def download_album(req, path):
    """Downloads an imgur album as a zip file and extracts it."""
    with zipfile.ZipFile(io.BytesIO(req.content)) as file:
        file.extractall(path)


def route_posts(posts, albums, gifs, nsfw, path):
    """Routes reddit posts to the correct download function."""
    for post in posts:
        # ignore sticky posts and self posts
        if post.stickied or post.is_self:
            continue
        # check for nsfw
        if post.over_18 and not nsfw:
            continue

        url = post.url
        # check for imgur album to set url
        if '/a/' in url:
            if not albums:
                print(f'[-] Ignoring album {url}')
                continue
            url = f'{url}/zip'
        # check for direct image, get direct image link if not
        else:
            if not url.lower().endswith(IMAGE_FORMATS):
                url = get_image_url(url)
                # if no direct image link was found, continue
                if not url:
                    continue
        # check for gif
        if url.endswith(('.gif', '.gifv')) and not gifs:
            print(f'[-] Ignoring gif {url}')
            continue

        req = get_request(url)
        if req is None:
            continue

        # check for imgur album
        if '/a/' in url:
            download = download_album(req, path)
        else:
            download = download_image(req, path)
        print(f'[+] Downloaded {post.title}')


class ImgBot():
    """Downloads images from subreddits.
    Default path is current directory, can be set globally in init
    or per download with the path keyword argument.

    Pass auth as kwargs. To use praw.ini use key name 'site_name',
    otherwise use 'client_id', 'client_secret', and 'user_agent'.

    Example usage:
        >> bot = imgbot.ImgBot(site_name='imgbot')
        >> bot('pics')
        [+] Downloaded ...
    """
    def __init__(self, path='.', **auth):
        self.path = path
        self.reddit = praw.Reddit(**auth)


    def get_subreddit_posts(self, sub, sort='hot', lim=10):
        """Takes a subreddit and returns an iterable of sorted posts.
        Use sort='topyear', topmonth', 'topweek', 'topday', or 'tophour'
        to get sorted top posts.
        """
        subreddit = self.reddit.subreddit(sub)
        subreddit_sorter = {
            'hot': subreddit.hot,
            'top': subreddit.top,
            'new': subreddit.new,
            'ris': subreddit.rising,
            'con': subreddit.controversial
        }
        if sort.endswith(('year', 'month', 'week', 'day', 'hour')):
            # slice time_filter from top
            sorted_posts = subreddit.top(limit=lim, time_filter=sort[3:])
        else:
            sorted_posts = subreddit_sorter[sort](limit=lim)

        return sorted_posts


    def download(self, *sub, sort='hot', lim=10, albums=True,
                gifs=True, nsfw=False, path=None):
        """Downloads images from a subreddit.
        Args:
            sub (str, tuple, list): subreddit(s) to download from
            sort (str): sorting method of subreddit
            lim (int): limit of posts to download
            albums (bool): download albums or not
            gifs (bool): download gifs or not
            nsfw (bool): download nsfw or not
            path (string): download path
        """

        if path is None:
            path = self.path
        # support multiple subs with multiprocessing
        if len(sub) > 1:
            # create a partial preserving kwargs to use with map
            f = functools.partial(self.download, sort=sort, lim=lim, albums=albums,
                                gifs=gifs, nsfw=nsfw, path=path)
            p = multiprocessing.Pool()
            p.map(f, sub)
            p.close()
            p.join()
        else:
            posts = self.get_subreddit_posts(sub[0], sort, lim)
            route_posts(posts, albums, gifs, nsfw, path)

    def __call__(self, *args, **kwargs):
        self.download(*args, **kwargs)
