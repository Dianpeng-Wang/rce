#!/usr/bin/env python
# -*- coding: utf-8 -*-
#     
#     manager.py
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
#     Copyright 2012 RoboEarth
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

# Python specific imports
from threading import Lock

# zope specific imports
from zope.interface import implements

# twisted specific imports
from twisted.python import log
from twisted.internet.defer import Deferred
from twisted.internet.task import LoopingCall

# Custom imports
from errors import InternalError, SerializationError
from util.interfaces import verifyObject
from comm import definition
from comm import types
from comm.interfaces import IContentSerializer, IMessageProcessor
from comm.handler import send
from comm.fifo import ProducerFIFO
validateAddress = definition.validateAddress

class _Router(object):
    """ Class which is responsible for routing the communication.
    """
    def __init__(self):
        """ Initialize the Router.
        """
        # Key:    Destination ID of connection endpoint
        # Value:  Connection/Protocol instances
        self._connections = {}
        
        # Key:    Destination ID of message endpoint
        # Value:  FIFOs where Producers are stored
        self._fifos = {}
        
        # Key:    Destination ID of message endpoint
        # Value:  Destination ID of connection endpoint
        #         which should be used as next waypoint
        self._routes = {}
        
        # Key:    Destination ID of connection endpoint
        # Value:  Set of Destination IDs of message endpoints
        #         which should be routed through this connection
        self._dest = {}
        
        # Destination ID of default connection endpoint or None
        self._default = None
        
        # Setup periodic calling of clean method of manager
        LoopingCall(self._clean).start(definition.MSG_QUEUE_TIMEOUT / 4)
    
    def registerConnection(self, conn):
        """ Callback for Protocol instance to register the connection.

            @param conn:    Protocol instance which has established the
                            connection.
            @type  conn:    comm.factory._RCEProtocol
        """
        dest = conn.dest
        
        log.msg('Router: Register connection from {0}'.format(dest))
        
        if dest in self._connections:
            raise InternalError('There is already a connection registered for '
                                'the same destination.')
        
        # Register connection
        self._connections[dest] = conn
        
        if dest not in self._fifos:
            # If there is no FIFO yet for this destination create one
            self._fifos[dest] = ProducerFIFO(dest)
        
        if dest not in self._dest:
            self._dest[dest] = set()
        
        # Add direct route to routing table
        self._routes[dest] = dest
        self._dest[dest].add(dest)
        
        # Try to start to send messages if it is necessary
        self._connections[dest].requestSend()
    
    def unregisterConnection(self, conn):
        """ Callback for Protocol instance to unregister the connection.

            @param conn:    Protocol instance which should be unregistered.
            @type  conn:    comm.factory._RCEProtocol
        """
        self._connections.pop(conn.dest, None)
    
    def registerProducer(self, producer, dest):
        """ Register a producer for a given destination.
            
            @param producer:    Producer which should be registered.
            @type  producer:    MessageSender or MessageForwarder

            @param dest:    The destination address has to be the valid
                            CommID of the destination node.
            @type  dest:    str
        """
        if not validateAddress(dest, True):
            log.msg('Message could not be sent: '
                    '"{0}" is not a valid address'.format(dest))
            return
        
        if dest not in self._fifos:
            # If there is no FIFO yet for this destination create one
            self._fifos[dest] = ProducerFIFO(dest)
        
        # Add message to the queue
        self._fifos[dest].add(producer)
        
        # Try to send the message
        if dest in self._routes:
            route = self._routes[dest]
        elif dest in definition.DEFAULT_EXCEMPT_ADDRS:
            return
        elif self._default:
            route = self._default
        else:
            return
        
        if route in self._connections:
            self._connections[route].requestSend()
    
    def updateRoutingInfo(self, route, dests):
        """ Callback for the RoutingInfo processor to update the routing
            information in this CommManager.
            
            @param route:   Destination address which should be used as next
                            routing point for all the destination in 'dests'.
            @type  route:   str
            
            @param dests:   List of 2-tuples with all destination IDs which
                            should be routed through 'route' as first element
                            of the tuples. The destination IDs have to be
                            either valid CommIDs or None, which is used as a
                            default routing address. The second element of the
                            tuple is a flag to indicate whether the routing
                            should be added (True) or removed (False).
            @type  dests:   [ (str, bool) ]
        """
        for (dest, add) in dests:
            if dest is None:
                if add:
                    log.msg('Add default route via {0}.'.format(route))
                    self._default = route
                else:
                    log.msg('Remove default route via {0}.'.format(route))
                    self._default = None
            else:
                if add:
                    log.msg('Add route to {0} via {1}.'.format(dest, route))
                    self._routes[dest] = route
                    self._dest[route].add(dest)
                else:
                    log.msg('Remove route to {0} via {1}.'.format(dest, route))
                    
                    try:
                        del self._routes[dest]
                    except KeyError:
                        pass
                    
                    try:
                        self._dest[route].remove(dest)
                    except KeyError:
                        pass
        
        if route in self._connections:
            self._connections[route].requestSend()
    
    def getNextProducer(self, route):
        """ Callback for the protocol instances which is used when a new
            producer can be processed.
            
            @param route:   Communication ID of destination of protocol.
            @type  route:   str
            
            @return:        Next producer which should be processed or None if
                            no processor are left to process.
            @rtype:         comm.handler._MessageSender or
                            comm.handler._MessageForwarder or
                            None
        """
        oldestFifo = None
        oldestTimestamp = None
        
        if route == self._default:
            dests = self._fifos.keys()
        else:
            dests = self._dest[route]
        
        while 1:  # This while loop is necessary for: route == producer.origin
            for dest in dests:
                if dest not in self._fifos:
                    continue
                
                fifo = self._fifos[dest]
                
                if not len(fifo):
                    continue
                
                timestamp = fifo.getTimeStamp()
                    
                if oldestTimestamp is None or timestamp < oldestTimestamp:
                    oldestTimestamp = timestamp
                    oldestFifo = fifo
            
            if oldestFifo:
                producer = oldestFifo.pop()
                
                if route != producer.origin:
                    return producer
                else:
                    # If the message would be sent back to where it came from,
                    # drop it and try to get the next producer
                    log.msg('Tried to return a message to the sender. '
                            'Message will be dropped.')
            else:
                return None
    
    def _clean(self):
        """ Method is used to clean up producer queue to remove old entries.
        """
        for fifo in self._fifos.itervalues():
            fifo.clean()


class _InitMessage(object):
    """ Message content type to initialize the communication.
        
        The fields are:
            data    Data which should be sent along with the request
    """
    implements(IContentSerializer)
    
    IDENTIFIER = types.INIT_REQUEST
    
    def serialize(self, s, msg):
        try:
            data = msg['data']
        except KeyError:
            raise SerializationError('Could not serialize content of '
                                     "Init Message. Missing key: 'data'")
        
        if not isinstance(data, list):
            raise SerializationError('Data of Init Message has to be a list.')
        
        s.addList(data)
    
    def deserialize(self, s):
        return { 'data' : s.getList() }


class _StatusMessage(object):
    """ Message content type to send a status message to another process in
        the RCE.
        
        The fields are:
            msg     Message which should be delivered
            id      Message number to which this message belongs
    """
    implements(IContentSerializer)
    
    IDENTIFIER = types.STATUS
    
    def serialize(self, s, msg):
        try:
            s.addElement(msg['msg'])
            s.addInt(msg['id'])
        except KeyError:
            raise SerializationError('Could not serialize content of '
                                     "Status Message. Missing key: 'data'")
    
    def deserialize(self, s):
        return { 'msg' : s.getElement(), 'id' : s.getInt() }


class _ErrorMessage(object):
    """ Message content type to send an error message to another process in
        the RCE.
        
        The fields are:
            msg     Message which should be delivered
            id      Message number to which this message belongs
    """
    implements(IContentSerializer)
    
    IDENTIFIER = types.ERROR
    
    def serialize(self, s, msg):
        try:
            s.addElement(msg['msg'])
            s.addInt(msg['id'])
        except KeyError:
            raise SerializationError('Could not serialize content of '
                                     "Error Message. Missing key: 'data'")
    
    def deserialize(self, s):
        return { 'msg' : s.getElement(), 'id' : s.getInt() }


class _RouteMessage(object):
    """ Message content type to provide other nodes with routing information.
        
        Message format is a list of 2-tuples
            (DestID, Add (True) / Remove (False) flag)
    """
    implements(IContentSerializer)
    
    IDENTIFIER = types.ROUTE_INFO
    
    def serialize(self, s, msg):
        if not isinstance(msg, list):
            raise SerializationError('Content of the message has to be '
                                     'a list.')
        
        s.addInt(len(msg))
        
        for element in msg:
            if len(element) != 2:
                raise SerializationError('List element is not a 2-tuple.')
            
            try:
                s.addElement(element[0])
                s.addBool(element[1])
            except IndexError as e:
                raise SerializationError('Could not serialize element: '
                                         '{0}'.format(e))
    
    def deserialize(self, s):
        return [(s.getElement(), s.getBool()) for _ in xrange(s.getInt())]


class _StatusProcessor(object):
    """ Message processor to handle a status message.
    """
    implements(IMessageProcessor)
    
    IDENTIFIER = types.STATUS
    
    def __init__(self, manager):
        """ @param manager:     CommManager which is used in this node.
            @type  manager:     comm.manager.CommManager
        """
        self.manager = manager
    
    def processMessage(self, msg, _):
        self.manager.processStatusMessage(msg.content)


class _ErrorProcessor(object):
    """ Message processor to handle an error message.
    """
    implements(IMessageProcessor)
    
    IDENTIFIER = types.ERROR
    
    def __init__(self, manager):
        """ @param manager:     CommManager which is used in this node.
            @type  manager:     comm.manager.CommManager
        """
        self.manager = manager
    
    def processMessage(self, msg, _):
        self.manager.processErrorMessage(msg.content)


class _RouteProcessor(object):
    """ Message processor to update the routing information.
    """
    implements(IMessageProcessor)
    
    IDENTIFIER = types.ROUTE_INFO
    
    def __init__(self, manager):
        """ @param manager:     CommManager which is used in this node.
            @type  manager:     comm.manager.CommManager
        """
        self.manager = manager
    
    def processMessage(self, msg, _):
        self.manager.router.updateRoutingInfo(msg.origin, msg.content)


class CommManager(object):
    """ Class which is responsible for handling the communication.
    """
    def __init__(self, reactor, commID):
        """ Initialize the CommManager.

            @param reactor:     Reference to used reactor (from twisted).
            @type  reactor:     twisted::reactor

            @param commID:  CommID of this node which can be used by other
                            nodes to identify this node.
            @type  commID:  str
        """
        # Communication ID of this node
        self._commID = commID
        
        # twisted reactor which is used in this manager
        self._reactor = reactor
        
        # Reference to Router
        self._router = _Router()
        
        # Dictionary containing pending response deferreds (keys: msgNr as int)
        self._responses = {}
        
        # Message number counter
        self._msgNr = 0
        
        # Lock to be sure of thread-safety
        self._msgNrLock = Lock()
        
        # Content serializers
        self._contentSerializer = {}
        self.registerContentSerializers([_InitMessage(), _StatusMessage(),
                                         _ErrorMessage(), _RouteMessage()])
        
        # Message processors
        self._processors = {}
        self.registerMessageProcessors([_StatusProcessor(self),
                                        _ErrorProcessor(self),
                                        _RouteProcessor(self)])
    
    @property
    def commID(self):
        """ Communication ID of this node. """
        return self._commID
    
    @property
    def reactor(self):
        """ Reactor instance of this node. """
        return self._reactor
    
    @property
    def router(self):
        """ Router instance of this node. """
        return self._router
    
    def registerContentSerializers(self, serializers):
        """ Method to register a content serializer which are used for
            serializing/deserializing the messages. If there already exists a
            serializer for the same message type the old serializer is dropped.
            
            @param serializers:     Content serializers which should be
                                    registered.
            @type  serializers:     [ comm.interfaces.IContentSerializer ]
            
            @raise:     util.interfaces.InterfaceError if the serializers do
                        not implement comm.interfaces.IContentSerializer.
        """
        for serializer in serializers:
            verifyObject(IContentSerializer, serializer)
            self._contentSerializer[serializer.IDENTIFIER] = serializer
    
    def registerMessageProcessors(self, processors):
        """ Method to register a message processor which are used for the
            incoming messages. If there already exists a message processor for
            the same message type the old message processor is dropped.
                                
            @param processors:  Processor which should be registered.
            @type  processors:  [ comm.interfaces.IMessageProcessor ]
            
            @raise:     util.interfaces.InterfaceError if the processors do not
                        implement comm.interfaces.IContentSerializer.errors.
        """
        for processor in processors:
            verifyObject(IMessageProcessor, processor)
            self._processors[processor.IDENTIFIER] = processor
    
    def getMessageNr(self):
        """ Returns a new message number.
        """
        with self._msgNrLock:
            nr = self._msgNr
            self._msgNr = (self._msgNr + 1) % definition.MAX_INT
        
        return nr
    
    def getSerializer(self, msgType):
        """ Returns the ContentSerializer matching the given msgType or None if
            there was no match.
        """
        return self._contentSerializer.get(msgType, None)
    
    def sendMessage(self, msg, resp=None):
        """ Send a message.

            @param msg:     Message which should be sent.
            @type  msg:     comm.message.Message
            
            @param resp:    Optional argument which has to be a Deferred and
                            will be called when a response to message that
                            should be sent is received. When a status message
                            is received the deferred's callback is used and
                            the deferred's errback when an error message is
                            received.
            @type  resp:    twisted::Deferred
        """
        # Check if the response object is a Deferred
        if resp and not isinstance(resp, Deferred):
            raise InternalError('Response handler is not a Deferred.')
        
        # First check if the message has to be sent at all
        if msg.dest == self._commID:
            log.msg('Warning: Tried to send a message to myself.')
            self.processMessage(msg, resp or Deferred())
            return
        
        # Register Deferred object if necessary
        if resp:
            nr = self.getMessageNr()
            msg.msgNr = nr
            
            if nr in self._responses:
                raise InternalError('Tried to register a response deferred, '
                                    'but there is already one registered.')
            
            self._responses[nr] = resp
        
        # Try to send the message
        try:
            buf = msg.serialize(self)
        except SerializationError as e:
            log.msg('Message could not be sent: {0}'.format(e))
        else:
            send(self, buf, msg.dest)
    
    def processMessage(self, msg, resp):
        """ Process the message using the registered message processors.
            
            @param msg:     Received message.
            @type  msg:     comm.message.Message
            
            @param resp:    Deferred which will send a status message when
                            callback is used and a error message when errback
                            is used as a response to the sender.
            @type  resp:    twisted::Deferred
        """
        if msg.msgType in self._processors:
            self._processors[msg.msgType].processMessage(msg, resp)
        else:
            log.msg('Received a message whose type ("{0}") can not be handled '
                    'by this CommManager.'.format(msg.msgType))
    
    def processStatusMessage(self, msg):
        """ Callback from _StatusProcessor.
            
            @param msg:     Status message to process.
            @type  msg:     comm.message.Message
        """
        deferred = self._responses.pop(msg['nr'], None)
        
        if deferred:
            deferred.callback(msg['msg'])
    
    def processErrorMessage(self, msg):
        """ Callback from _ErrorProcessor.
            
            @param msg:     Error message to process.
            @type  msg:     comm.message.Message
        """
        deferred = self._responses.pop(msg['nr'], None)
        
        if deferred:
            deferred.errback(msg['msg'])
    
    def shutdown(self):
        """ Method which should be used when the CommManager is terminated.
            It is used to remove circular references.
        """
        self._contentSerializer = {}
        self._processors = {}
        self._responses = {}
