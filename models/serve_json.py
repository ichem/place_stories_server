import simplejson
from http_utils import json_to_storage
import datetime

class User_Error(Exception): #todo: the "_" is because of soon obsolete code. remove it when 

    def __init__(self, message, location=None):
        Exception.__init__(self, message)
        self.location = location

def serve_json(func):

    def f():
        t0 = datetime.datetime.now()
        vars = request.vars
        for k in vars:
            s = vars[k]
            if isinstance(s, str) and (s.startswith('{') or s.startswith('[')):
                vars[k] = simplejson.loads(s)
        vars = json_to_storage(vars)
        try:
            result = func(vars)
        except HTTP, e:
            if e.status == 303:
                result = e.headers
            else:
                log_exception('Error serving ' + func.__name__)
                result = dict(error=str(e))
        except User_Error, e:
            log_exception('User error serving ' + func.__name__)
            result = dict(user_error=str(e))
        except Exception, e:
            log_exception('Error serving ' + func.__name__)
            result = dict(error=str(e))
        try:
            result = response.json(result)
        except Exception, e:
            result = dict(warning=str(e), result=str(result))
            result = response.json(result)
        return result

    return f