from locust import events
import time
import csv
import re
import logging
import random
import string
import json
import socket
from logging.handlers import RotatingFileHandler
from datetime import datetime
from util.conf import JIRA_SETTINGS, CONFLUENCE_SETTINGS, AppSettingsExtLoadExecutor
from util.project_paths import ENV_TAURUS_ARTIFACT_DIR
from locust import exception
import inspect
from locust import TaskSet

TEXT_HEADERS = {
        'Accept-Language': 'en-US,en;q=0.5',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept-Encoding': 'gzip, deflate',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                }
RESOURCE_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest"
}
ADMIN_HEADERS = {
    'Accept-Language': 'en-US,en;q=0.5',
    'X-AUSERNAME': 'admin',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept-Encoding': 'gzip, deflate',
    'Accept': '*/*'
}
NO_TOKEN_HEADERS = {
    "Accept-Language": "en-US,en;q=0.5",
    "X-Requested-With": "XMLHttpRequest",
    "__amdModuleName": "jira/issue/utils/xsrf-token-header",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Atlassian-Token": "no-check"
}
JSON_HEADERS = {
    "Accept-Language": "en-US,en;q=0.5",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json, text/javascript, */*; q=0.01"
}

jira_action_time = 3600 / int((JIRA_SETTINGS.total_actions_per_hour) / int(JIRA_SETTINGS.concurrency))
confluence_action_time = 3600 / int((CONFLUENCE_SETTINGS.total_actions_per_hour) / int(CONFLUENCE_SETTINGS.concurrency))


class LocustConfig:

    def __init__(self, config_yml: AppSettingsExtLoadExecutor):
        self.env = config_yml.env
        self.secure = config_yml.secure

    def percentage(self, action_name: str):
        if action_name in self.env:
            return int(self.env[action_name])
        else:
            raise Exception(f'Action percentage for {action_name} does not set in yml configuration file')


class Logger(logging.Logger):

    def __init__(self, name, level, app_type):
        super().__init__(name=name, level=level)
        self.type = app_type

    def locust_info(self, msg, *args, **kwargs):
        is_verbose = False
        if self.type.lower() == 'confluence':
            is_verbose = CONFLUENCE_SETTINGS.verbose
        elif self.type.lower() == 'jira':
            is_verbose = JIRA_SETTINGS.verbose
        if is_verbose or not self.type:
            if self.isEnabledFor(logging.INFO):
                self._log(logging.INFO, msg, args, **kwargs)


class MyBaseTaskSet(TaskSet):

    cross_action_storage = dict()  # Cross actions locust storage
    login_failed = False

    def failure_check(self, response, action_name):
        if hasattr(response, 'error') or not response:
            if 'login' in action_name:
                self.login_failed = True
            events.request_failure.fire(request_type="Action",
                                        name=f"locust_{action_name}",
                                        response_time=0,
                                        response_length=0,
                                        exception=str(response.raise_for_status()))

    def get(self, *args, **kwargs):
        r = self.client.get(*args, **kwargs)
        action_name = inspect.stack()[1][3]
        self.failure_check(response=r, action_name=action_name)
        return r

    def post(self, *args, **kwargs):
        r = self.client.post(*args, **kwargs)
        action_name = inspect.stack()[1][3]
        self.failure_check(response=r, action_name=action_name)
        return r


def jira_measure(func):
    def wrapper(*args, **kwargs):
        start = time.time()
        result = global_measure(func, start, *args, **kwargs)
        total = (time.time() - start)
        if total < jira_action_time:
            sleep = (jira_action_time - total)
            print(f'action: {func.__name__}, action_execution_time: {total}, sleep {sleep}')
            time.sleep(sleep)
        return result
    return wrapper


def confluence_measure(func):
    def wrapper(*args, **kwargs):
        start = time.time()
        result = global_measure(func, start, *args, **kwargs)

        total = (time.time() - start)
        if total < confluence_action_time:
            sleep = (confluence_action_time - total)
            logger.info(f'action: {func.__name__}, action_execution_time: {total}, sleep {sleep}')
            time.sleep(sleep)
        return result
    return wrapper


def global_measure(func, start_time, *args, **kwargs):
    result = None
    try:
        result = func(*args, **kwargs)
    except Exception as e:
        total = int((time.time() - start_time) * 1000)
        print(e)
        events.request_failure.fire(request_type="Action",
                                    name=f"locust_{func.__name__}",
                                    response_time=total,
                                    response_length=0,
                                    exception=e)
        logger.error(f'{func.__name__} action failed. Reason: {e}')
    else:
        total = int((time.time() - start_time) * 1000)
        events.request_success.fire(request_type="Action",
                                    name=f"locust_{func.__name__}",
                                    response_time=total,
                                    response_length=0)
        logger.info(f'{func.__name__} is finished successfully')
    return result


def read_input_file(file_path):
    with open(file_path, 'r') as fs:
        reader = csv.reader(fs)
        return list(reader)


def fetch_by_re(pattern, text, group_no=1, default_value=None):
    search = re.search(pattern, text)
    if search:
        return search.group(group_no)
    else:
        return default_value


def read_json(file_json):
    with open(file_json) as f:
        return json.load(f)


def init_logger(app_type=None):
    logfile_path = ENV_TAURUS_ARTIFACT_DIR / 'locust.log'
    root_logger = Logger(name='locust', level=logging.INFO, app_type=app_type)
    log_format = f"[%(asctime)s.%(msecs)03d] [%(levelname)s] {socket.gethostname()}/%(name)s : %(message)s"
    formatter = logging.Formatter(log_format, '%Y-%m-%d %H:%M:%S')
    file_handler = RotatingFileHandler(logfile_path, maxBytes=5 * 1024 * 1024, backupCount=3)
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    root_logger.addHandler(file_handler)
    return root_logger


def timestamp_int():
    now = datetime.now()
    return int(datetime.timestamp(now))


def generate_random_string(length, only_letters=False):
    if not only_letters:
        return "".join([random.choice(string.digits + string.ascii_letters + ' ') for _ in range(length)])
    else:
        return "".join([random.choice(string.ascii_lowercase + ' ') for _ in range(length)])


def get_first_index(from_list: list, err):
    if len(from_list) > 0:
        return from_list[0]
    else:
        raise IndexError(err)


def raise_if_login_failed(locust):
    if locust.login_failed:
        raise exception.StopUser('Action login_and_view_dashboard failed')


logger = init_logger()
