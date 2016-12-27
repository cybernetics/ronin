# Copyright 2016-2017 Tal Liron
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from .utils.types import verify_type
from cStringIO import StringIO
from collections import OrderedDict
import threading, sys

_thread_locals = threading.local()

def new_build_context(*args, **kwargs):
    """
    Creates a new context and calls :code:`configure_build` on it.
    """
    
    from .configuration import configure_build
    ctx = new_context()
    ctx._push_thread_local()
    try:
        configure_build(*args, frame=2, **kwargs)
    finally:
        Context._pop_thread_local()
    return ctx

def new_context():
    """
    Creates a new context.
    
    If there already is a context in this thread, our new context will be a child of that context.
    """
    
    ctx = Context._peek_thread_local()
    return Context(ctx)

def current_context(immutable=True):
    """
    Uses the current context if there is one. If there is none, raises a
    :class:`NoContextException`.
    
    By default, the context will be treated as immutable.
    """

    ctx = Context._peek_thread_local()
    if ctx is None:
        raise NoContextException()
    return Context(ctx, True) if immutable else ctx

class Context(object):
    """
    Keeps track of environmental and user configuration properties per run.
    
    Designed to be attached to a single thread. Supports nesting contexts within the thread: a
    child context will return its parent's properties if it does not define them itself. 
    
    If the context is immutable it will raise :class:`ImmutableContextException` if you try to
    modify any of the properties.
    """
    
    LOCAL = ('_parent', '_immutable', '_namespaces')
    
    def __init__(self, parent=None, immutable=False):
        if parent:
            verify_type(parent, Context)
        self._parent = parent
        self._immutable = immutable
        self._namespaces = {}
    
    def __str__(self):
        io = StringIO()
        try:
            self._write(io)
            v = io.getvalue()
        finally:
            io.close()
        return v

    def __enter__(self):
        self._push_thread_local()
        return self
    
    def __exit__(self, the_type, value, traceback):
        self._pop_thread_local()

    def __getattr__(self, name):
        if name in self.LOCAL:
            raise RuntimeError('context not initialized?')
        namespace = self._namespaces.get(name)
        if namespace is None:
            namespace = _Namespace(name, self)
            self._namespaces[name] = namespace
        return namespace

    def __setattr__(self, name, value):
        if name not in self.LOCAL:
            raise IncorrectUseOfContextException('namespaces cannot be assigned values: "%s"' % name)
        super(Context, self).__setattr__(name, value)

    def get(self, name, default=None):
        """
        Gets a value from the context or :code:`default` if the undefined.
        
        Note that if the value is defined as is :code:`None`, then :code:`None` is returned
        and :code:`default`!
        """
        
        if '.' not in name:
            return default
        namespace_name, name = name.split('.', 2)
        try:
            namespace = getattr(self, namespace_name)
            return getattr(namespace, name)
        except NotInContextException:
            return default

    def fallback(self, value, name, default=None):
        """
        If the value is not :code:`None`, returns it. If it is :code:`None`, works identically
        to :code:`get`.
        """
        
        if value is None:
            return self.get(name, default)
        return value
    
    def append_to_import_path(self, name, default=None):
        path = self.get(name, default)
        if path is not None:
            sys.path.append(path)
    
    @property
    def _all(self):
        r = OrderedDict()
        if self._parent:
            r.update(self._parent._all)
        for namespace_name, namespace in sorted(self._namespaces.items()):
            for k, v in sorted(namespace._all_local.items()):
                r['%s.%s' % (namespace_name, k)] = v
        return r

    def _write(self, io):
        for k, v in self._all.iteritems():
            if not k.startswith('_'):
                io.write('%s=%s\n' % (k, v))

    def _push_thread_local(self):
        """
        Attaches this context to the current thread by pushing it on the stack.
        """

        try:
            stack = _thread_locals.ronin_context_stack
        except AttributeError:
            stack = _ContextStack()
            _thread_locals.ronin_context_stack = stack
        stack.push(self)

    @staticmethod
    def _peek_thread_local():
        """
        Gets the context attached to the current thread if there is one, which will be the top
        context on the stack.
        """

        try:
            return _thread_locals.ronin_context_stack.peek()
        except AttributeError:
            return None

    @staticmethod
    def _pop_thread_local():
        """
        Removes the context attached to the current thread if there is one, which will be the top
        context on the stack.
        """
        
        try:
            _thread_locals.ronin_context_stack.pop()
        except AttributeError:
            return None

class ContextException(Exception):
    """
    Base class for context excpetions.
    """

    def __init__(self, message=None):
        super(ContextException, self).__init__(message)

class NoContextException(ContextException):
    def __init__(self, message=None):
        super(NoContextException, self).__init__(message)

class NotInContextException(ContextException):
    def __init__(self, message=None):
        super(NotInContextException, self).__init__(message)

class ImmutableContextException(ContextException):
    def __init__(self, message=None):
        super(ImmutableContextException, self).__init__(message)

class IncorrectUseOfContextException(ContextException):
    def __init__(self, message=None):
        super(IncorrectUseOfContextException, self).__init__(message)

class _Namespace(object):
    LOCAL = ('_name', '_context')
    
    def __init__(self, name, context):
        self._name = name
        self._context = context

    @property
    def _all(self):
        r = OrderedDict()
        if self._context._parent:
            parent = getattr(self._context._parent, self._name)
            r.update(parent._all)
        r.update(self._all_local)
        return r

    @property
    def _all_local(self):
        r = OrderedDict()
        for k, v in sorted(vars(self).items()):
            if (k not in self.LOCAL) and (not k.startswith('_')):
                r[k] = v
        return r

    def __getattr__(self, name):
        if name in self.LOCAL:
            raise RuntimeError('namespace not initialized?')
        if self._context._parent is None:
            raise NotInContextException(name)
        parent = getattr(self._context._parent, self._name)
        return getattr(parent, name)

    def __setattr__(self, name, value):
        if name not in self.LOCAL:
            try:
                if self._context._immutable:
                    raise ImmutableContextException()
            except AttributeError:
                pass
        super(_Namespace, self).__setattr__(name, value)

class _ContextStack(object):
    """
    Manages a stack of :class:`Context` instances.
    """
    
    def __init__(self):
        self._stack = []
    
    def push(self, context):
        self._stack.append(context)
    
    def peek(self):
        return self._stack[-1] if len(self._stack) else None

    def pop(self):
        return self._stack.pop() if len(self._stack) else None
