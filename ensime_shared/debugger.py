# coding: utf-8

import json
import os
import tempfile

from ensime_shared.config import feedback

class DebugBreakpoint(object):
    def __init__(self, path, line):
        self.path = path
        self.line = line

    def __hash__(self):
        return hash((self.path, self.line))

    def __eq__(self, other):
        return self.__hash__() == other.__hash__()


class DebuggerClient(object):
    """This is the implementation of the Ensime debugger client, it must be mixed in
       with the EnsimeClient to be useful."""

    def __init__(self):
        self.debug_breakpoints = {}
        super(DebuggerClient, self).__init__()

    def __refresh_debug_signs(self):
        for bp in self.debug_breakpoints:
            path = bp.path
            line = bp.line
            self.editor._vim.command(
                'sign unplace {line} file={path}'.format(line=line, path=path))
            self.editor._vim.command(
                'sign place {line} name=EnDebugBreakpointSign line={line} file={path}'.format(
                    line=line, path=path))

# Response Handlers
    def handle_debug_events(self, call_id, payload):
        self.log.debug('Generic debug event: in')

        typehint = payload['typehint']
        if typehint == 'DebugVmStartEvent':
            self.editor.raw_message('Debug VM started.')
        elif typehint == 'DebugVmDisconnectEvent':
            self.editor.raw_message('Debug VM disconnected.')
        else:
            self.log.debug('Did not handle debug event for {}.'.format(typehint))

    def handle_debug_output(self, call_id, payload):
        """Handle responses `DebugOutputEvent`."""
        self.editor.raw_message(payload["body"].encode("ascii", "ignore"))

    def handle_debug_break(self, call_id, payload):
        """Handle responses `DebugBreakEvent`."""
        line = payload['line']
        config = self.launcher.config
        path = os.path.relpath(payload['file'], config['root-dir'])

        self.editor.raw_message(feedback['notify_break'].format(line, path))
        self.debug_thread_id = payload["threadId"]

        self.__refresh_debug_signs()
        self.editor._vim.command(
            'sign unplace {line} file={path}'.format(line=line, path=path))
        self.editor._vim.command(
            'sign place {line} name=EnDebugCurrentBreakpointSign line={line} file={path}'.format(
                line=line, path=path))

    def handle_debug_backtrace(self, call_id, payload):
        """Handle responses `DebugBacktrace`."""
        frames = payload["frames"]

        def frame_info(frame):
            def local_info(local):
                local_line = '    {}: {}'.format(local['name'], local['typeName'])
                value_line = '      value: ' + local['summary']
                return '\n'.join([local_line, value_line])

            frame_line = 'Frame {idx}: {className}:{methodName}'.format(
                idx=frame['index'],
                className=frame['className'],
                methodName=frame['methodName'])
            file_line = '  File: {}:{}'.format(
                frame['pcLocation']['file'], frame['pcLocation']['line'])

            local_infos = '\n'.join([local_info(local) for local in frame['locals']])
            locals_lines = '  Locals:\n' + local_infos

            return '\n'.join([frame_line, file_line, locals_lines])

        frame_lines = '\n'.join([frame_info(frame) for frame in frames])

        fd, path = tempfile.mkstemp('debug', text=True, dir=self.tmp_diff_folder)
        tmpfile = os.fdopen(fd, 'w')
        tmpfile.write(frame_lines)
        tmpfile.close()

        opts = {
            'readonly': True,
            'bufhidden': 'wipe',
            'buflisted': False,
            'swapfile': False
        }
        self.editor.split_window(path, size=20, bufopts=opts)

    def handle_breakpoint_list(self, call_id, payload):
        active_bps = [DebugBreakpoint(bp['file'], bp['line']) for bp in payload['active']]
        pending_bps = [DebugBreakpoint(bp['file'], bp['line']) for bp in payload['pending']]
        editor_bps = [bp for bp in self.debug_breakpoints if bp not in active_bps + pending_bps]

        def format_bp(bp, state):
            return '{{\'filename\':\'{path}\',\'lnum\':{line},\'text\':\'{state} breakpoint\'}}'.format(
                path=bp.path, line=bp.line, state=state)

        bps = [format_bp(bp, 'active') for bp in active_bps] + \
              [format_bp(bp, 'pending') for bp in pending_bps] + \
              [format_bp(bp, 'editor') for bp in editor_bps]
        bp_str = ','.join(bps)

        self.editor._vim.command('call setqflist([{bps}], \'r\')'.format(
            bps=bp_str))

# API Call Build/Send
    def debug_set_break(self, args, range=None):
        path = self.editor.path()
        line = self.editor.cursor()[0]

        self.log.debug('Setting breakpoint request for {path}:{line}.'.format(
            path=path, line=line))

        req = {
            "line": line,
            "maxResults": 10,
            "typehint": "DebugSetBreakReq",
            "file": path
        }
        self.send_request(req)

        breakpoint = DebugBreakpoint(path, line)
        self.debug_breakpoints[breakpoint] = breakpoint

        self.__refresh_debug_signs()
        self.editor._vim.command(
            'sign place {line} name=EnDebugBreakpointSign line={line} file={path}'.format(
                line=line, path=path))

    def debug_clear_break(self, args, range=None):
        self.log.debug('debug_clear_breaks: in')

        path = self.editor.path()
        line = self.editor.cursor()[0]

        self.log.debug('Clear breakpoint request for {path}:{line}.'.format(
            path=path, line=line))

        req = {
            "line": line,
            "maxResults": 10,
            "typehint": "DebugClearBreakReq",
            "file": path
        }
        self.send_request(req)

        self.debug_breakpoints.pop(DebugBreakpoint(path, line), None)

        self.__refresh_debug_signs()
        self.editor._vim.command('sign unplace {line} file={path}'.format(
            line=line, path=path))

    def debug_clear_breaks(self, args, range=None):
        self.log.debug('debug_clear_breaks: in')
        self.send_request({"typehint": "DebugClearAllBreaksReq"})

        self.debug_breakpoints = {}

        self.__refresh_debug_signs()
        for breakpoint in self.debug_breakpoints:
            self.editor._vim.command('sign unplace {line} file={path}'.format(
                line=breakpoint.line, path=breakpoint.path))

    def debug_start(self, args, range=None):
        self.log.debug('debug_start: in')
        if len(args) > 1:
            self.send_request({
                "typehint": "DebugAttachReq",
                "hostname": args[0],
                "port": args[1]})
        else:
            self.send_request({
                "typehint": "DebugAttachReq",
                "hostname": "localhost",
                "port": "5005"})

    def debug_continue(self, args, range=None):
        self.log.debug('debug_continue: in')
        self.send_request({
            "typehint": "DebugContinueReq",
            "threadId": self.debug_thread_id})

    def debug_backtrace(self, args, range=None):
        self.log.debug('debug_backtrace: in')
        self.send_request({
            "typehint": "DebugBacktraceReq",
            "threadId": self.debug_thread_id,
            "index": 0, "count": 100})

    def debug_step(self, args, range=None):
        self.log.debug('debug_step: in')
        self.send_request({
            "typehint": "DebugStepReq",
            "threadId": self.debug_thread_id})

    def debug_step_out(self, args, range=None):
        self.log.debug('debug_step_out: in')
        self.send_request({
            "typehint": "DebugStepOutReq",
            "threadId": self.debug_thread_id})

    def debug_next(self, args, range=None):
        self.log.debug('debug_next: in')
        self.send_request({
            "typehint": "DebugNextReq",
            "threadId": self.debug_thread_id})

    def debug_list_breaks(self, args, range=None):
        self.log.debug('debug_list_breaks: in')
        self.send_request({"typehint": "DebugListBreakpointsReq"})
