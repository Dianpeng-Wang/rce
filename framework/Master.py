#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
#       Master.py
#       
#       Copyright 2012 dominique hunziker <dominique.hunziker@gmail.com>
#       
#       This program is free software; you can redistribute it and/or modify
#       it under the terms of the GNU General Public License as published by
#       the Free Software Foundation; either version 2 of the License, or
#       (at your option) any later version.
#       
#       This program is distributed in the hope that it will be useful,
#       but WITHOUT ANY WARRANTY; without even the implied warranty of
#       MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#       GNU General Public License for more details.
#       
#       You should have received a copy of the GNU General Public License
#       along with this program; if not, write to the Free Software
#       Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
#       MA 02110-1301, USA.
#       
#       

# zope specific imports
from zope.interface import implements

# twisted specific imports
from twisted.python import log
from twisted.internet.ssl import DefaultOpenSSLContextFactory
from twisted.internet.task import LoopingCall

# Python specific imports
import sys

# Custom imports
import settings
from Comm.Message import MsgDef
from Comm.Message import  MsgTypes
from Comm.Message.Base import Message
from Comm.Factory import RCEServerFactory
from Comm.CommManager import CommManager
from Comm.Interfaces import IPostInitTrigger
from MasterUtil.Manager import MasterManager
from MasterUtil.UIDServer import UIDServerFactory

class MasterTrigger(object):
    """ PostInitTrigger which is used to send the available server as connection directive.
    """
    implements(IPostInitTrigger)
    
    def __init__(self, commMngr, masterMngr):
        """ Initialize the DefaultRoutingTrigger.

            @param commMngr:    CommManager which is responsible for handling the communication
                                in this node.
            @type  commMngr:    CommManager
            
            @param masterMngr:  MasterManager which is responsible for the handling of this node.
            @type  masterMngr:  MasterManager
        """
        self.commManager = commMngr
        self.masterManager = masterMngr
    
    def trigger(self, origin, ip):
        msg = Message()
        msg.msgType = MsgTypes.CONNECT
        msg.dest = origin
        msg.content = self.masterManager.getServers()
        self.commManager.sendMessage(msg)
        
        self.masterManager.addServer(origin, ip)

class MasterServerFactory(RCEServerFactory):
    """ RCEServerFactory which is used in the master node for the connections to the
        server nodes.
    """
    def __init__(self, commMngr, masterMngr):
        """ Initialize the MasterServerFactory.

            @param commMngr:    CommManager which is responsible for handling the communication
                                in this node.
            @type  commMngr:    CommManager
            
            @param masterMngr:  MasterManager which is used in this node.
            @type  masterMngr:  MasterManager
        """
        super(MasterServerFactory, self).__init__(commMngr, MasterTrigger(commMngr, masterMngr))
        self._masterManager = masterMngr
    
    def authOrigin(self, origin):
        return self._masterManager.checkUID(origin[MsgDef.PREFIX_LENGTH_ADDR:])
    
    def unregisterConnection(self, conn):
        super(MasterServerFactory, self).unregisterConnection(conn)
        self._masterManager.removeServer(conn.dest)

def main(reactor):
    # Start logger
    log.startLogging(sys.stdout)
    
    log.msg('Start initialization...')
    #ctx = DefaultOpenSSLContextFactory('Comm/key.pem', 'Comm/cert.pem')

    # Create Manager
    commManager = CommManager(reactor, MsgDef.MASTER_ADDR)
    masterManager = MasterManager(commManager)
    
    # Initialize twisted
    log.msg('Initialize twisted')
    
    # Server for UID distribution
    reactor.listenTCP(settings.PORT_UID, UIDServerFactory(masterManager))
    
    # Server for connections from the servers
    factory = MasterServerFactory( commManager,
                                   masterManager )
    factory.addApprovedMessageTypes([ MsgTypes.ROUTE_INFO,  # TODO: <- Necessary?
                                      MsgTypes.LOAD_INFO,
                                      MsgTypes.ID_REQUEST,
                                      MsgTypes.ID_DEL ])
    #reactor.listenSSL(settings.PORT_MASTER, factory, ctx)
    reactor.listenTCP(settings.PORT_MASTER, factory)
    
    # Server for initial outside connections
    ### 
    ### TODO: Add outside connection here!
    ###
    
    # Setup periodic calling of Clean Up
    log.msg('Add periodic call for Clean Up.')
    LoopingCall(masterManager.clean).start(settings.UID_TIMEOUT / 2)
    
    # Setup shutdown hooks
    #reactor.addSystemEventTrigger('before', 'shutdown', masterManager.shutdown)
    reactor.addSystemEventTrigger('before', 'shutdown', commManager.shutdown)
    
    # Start twisted (without signal handles as ROS also registers signal handlers)
    log.msg('Initialization completed')
    log.msg('Enter mainloop')
    reactor.run()
    log.msg('Leaving Master')

if __name__ == '__main__':
    from twisted.internet import reactor
    main(reactor)
