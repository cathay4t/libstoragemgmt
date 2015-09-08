# Copyright (C) 2015 Red Hat, Inc.
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; If not, see <http://www.gnu.org/licenses/>.
#
# Author: Gris Ge <fge@redhat.com>

import traceback
from lsm import (LsmError, ErrorNumber, error)
from pywbem import CIMError
import pywbem
import json


def handle_cim_errors(method):
    def cim_wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except LsmError as lsm:
            raise
        except CIMError as ce:
            error_code, desc = ce

            if error_code == 0:
                if 'Socket error' in desc:
                    if 'Errno 111' in desc:
                        raise LsmError(ErrorNumber.NETWORK_CONNREFUSED,
                                       'Connection refused')
                    if 'Errno 113' in desc:
                        raise LsmError(ErrorNumber.NETWORK_HOSTDOWN,
                                       'Host is down')
                elif 'SSL error' in desc:
                    raise LsmError(ErrorNumber.TRANSPORT_COMMUNICATION,
                                   desc)
                elif 'The web server returned a bad status line':
                    raise LsmError(ErrorNumber.TRANSPORT_COMMUNICATION,
                                   desc)
                elif 'HTTP error' in desc:
                    raise LsmError(ErrorNumber.TRANSPORT_COMMUNICATION,
                                   desc)
            raise LsmError(ErrorNumber.PLUGIN_BUG, desc)
        except pywbem.cim_http.AuthError as ae:
            raise LsmError(ErrorNumber.PLUGIN_AUTH_FAILED, "Unauthorized user")
        except pywbem.cim_http.Error as te:
            raise LsmError(ErrorNumber.NETWORK_ERROR, str(te))
        except Exception as e:
            error("Unexpected exception:\n" + traceback.format_exc())
            raise LsmError(ErrorNumber.PLUGIN_BUG, str(e),
                           traceback.format_exc())
    return cim_wrapper
