#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#     rce-core/rce/core/container.py
#
#     This file is part of the RoboEarth Cloud Engine framework.
#
#     This file was originally created for RoboEearth
#     http://www.roboearth.org/
#
#     The research leading to these results has received funding from
#     the European Union Seventh Framework Programme FP7/2007-2013 under
#     grant agreement no248942 RoboEarth.
#
#     Copyright 2013 RoboEarth
#
#     Licensed under the Apache License, Version 2.0 (the "License");
#     you may not use this file except in compliance with the License.
#     You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS,
#     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#     See the License for the specific language governing permissions and
#     limitations under the License.
#
#     \author/s: Dominique Hunziker
#
#

# twisted specific imports
from twisted.internet.address import IPv4Address
from twisted.internet.defer import Deferred, succeed

# rce specific imports
from rce.core.base import Proxy


class Container(Proxy):
    """ Representation of an LXC container.
    """
    def __init__(self, machine, userID, data={}):
        """ Initialize the Container.

            @param machine:     Machine in which the container was created.
            @type  machine:     rce.core.machine.Machine

            @param userID:      ID of the user who created the container.
            @type  userID:      str

            @param data:        Extra data about the container
            @type  data:        dict
        """
        super(Container, self).__init__()
        self._userID = userID
        self._machine = machine

        self._size = data.get('size', 0)
        self._cpu_limit = data.get('cpu', 0)
        self._memory_limit = data.get('memory', 0)
        self._bandwidth_limit = data.get('bandwidth', 0)

        # Group networking fields
        self._group = data.get('group', '')
        self._groupIp = data.get('groupIp', '')

        # Register machine can then use these details for decisions, TODO
        machine.registerContainer(self)

        self._pending = set()
        self._address = None

    def getAddress(self):
        """ Get the address which should be used to connect to the environment
            process for the cloud engine internal communication. The method
            gets the address only once and caches the address for subsequent
            calls.

            @return:            twisted::IPv4Address which can be used to
                                connect to the ServerFactory of the cloud
                                engine internal communication protocol.
                                (type: twisted.internet.address.IPv4Address)
            @rtype:             twisted.internet.defer.Deferred
        """
        if self._address is None:
            if not self._pending:
                # This is the first time this method is called dispatch a call
                # to fetch the address
                def cb(result):
                    self._address = result

                    for p in self._pending:
                        p.callback(result)

                    self._pending = set()

                addr = self.callRemote('getPort')
                addr.addCallback(lambda port: IPv4Address('TCP',
                                                          self._machine.IP,
                                                          port))
                addr.addBoth(cb)

            d = Deferred()
            self._pending.add(d)
            return d

        return succeed(self._address)

    def destroy(self):
        """ Method should be called to destroy the container and will take care
            of deleting all circular references.
        """
        if self._machine:
            self._machine.unregisterContainer(self)
            self._machine = None

            super(Container, self).destroy()
        else:
            print('container.Container destroy() called multiple times...')
