__copyright__ = """
Copyright (C) 2005, Catalin Marinas <catalin.marinas@gmail.com>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License version 2 as
published by the Free Software Foundation.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307 USA
"""

import sys, os, re, time, smtplib, email.Utils
from optparse import OptionParser, make_option
from time import gmtime, strftime

from stgit.commands.common import *
from stgit.utils import *
from stgit import stack, git
from stgit.config import config


help = 'send a patch or series of patches by e-mail'
usage = """%prog [options] [<patch>]

Send a patch or a range of patches (defaulting to the applied patches)
by e-mail using the 'smtpserver' configuration option. The From/To/Cc
addresses and the e-mail format are generated from the template file
passed as argument to '--template' (defaulting to
.git/patchmail.tmpl). A preamble e-mail can also be sent using the
'--first' option (no default template).

All the subsequent e-mails appear as replies to the first e-mail sent
(either the preamble or the first patch). E-mails can be seen as
replies to a different e-mail by using the '--refid' option.

SMTP authentication is also possible with '--smtp-user' and
'--smtp-password' options, also available as configuration settings:
'smtpuser' and 'smtppassword'.

The template e-mail headers and body must be separated by
'%(endofheaders)s' variable, which is replaced by StGIT with
additional headers and a blank line. The patch e-mail template accepts
the following variables:

  %(patch)s        - patch name
  %(shortdescr)s   - the first line of the patch description
  %(longdescr)s    - the rest of the patch description, after the first line
  %(endofheaders)s - delimiter between e-mail headers and body
  %(diff)s         - unified diff of the patch
  %(diffstat)s     - diff statistics
  %(date)s         - current date/time
  %(patchnr)s      - patch number
  %(totalnr)s      - total number of patches to be sent
  %(authname)s     - author's name
  %(authemail)s    - author's email
  %(authdate)s     - patch creation date
  %(commname)s     - committer's name
  %(commemail)s    - committer's e-mail

For the preamble e-mail template, only the %(date)s, %(endofheaders)s
and %(totalnr)s variables are supported."""

options = [make_option('-a', '--all',
                       help = 'e-mail all the applied patches',
                       action = 'store_true'),
           make_option('-r', '--range',
                       metavar = '[PATCH1][:[PATCH2]]',
                       help = 'e-mail patches between PATCH1 and PATCH2'),
           make_option('-t', '--template', metavar = 'FILE',
                       help = 'use FILE as the message template'),
           make_option('-f', '--first', metavar = 'FILE',
                       help = 'send FILE as the first message'),
           make_option('-s', '--sleep', type = 'int', metavar = 'SECONDS',
                       help = 'sleep for SECONDS between e-mails sending'),
           make_option('--refid',
                       help = 'Use REFID as the reference id'),
           make_option('-u', '--smtp-user', metavar = 'USER',
                       help = 'username for SMTP authentication'),
           make_option('-p', '--smtp-password', metavar = 'PASSWORD',
                       help = 'username for SMTP authentication')]


def __parse_addresses(string):
    """Return a two elements tuple: (from, [to])
    """
    def __addr_list(string):
        return re.split('.*?([\w\.]+@[\w\.]+)', string)[1:-1:2]

    from_addr_list = []
    to_addr_list = []
    for line in string.split('\n'):
        if re.match('from:\s+', line, re.I):
            from_addr_list += __addr_list(line)
        elif re.match('(to|cc|bcc):\s+', line, re.I):
            to_addr_list += __addr_list(line)

    if len(from_addr_list) != 1:
        raise CmdException, 'No "From" address'
    if len(to_addr_list) == 0:
        raise CmdException, 'No "To/Cc/Bcc" addresses'

    return (from_addr_list[0], to_addr_list)

def __send_message(smtpserver, from_addr, to_addr_list, msg, sleep,
                   smtpuser, smtppassword):
    """Send the message using the given SMTP server
    """
    try:
        s = smtplib.SMTP(smtpserver)
    except Exception, err:
        raise CmdException, str(err)

    s.set_debuglevel(0)
    try:
        if smtpuser and smtppassword:
            s.ehlo()
            s.login(smtpuser, smtppassword)

        s.sendmail(from_addr, to_addr_list, msg)
        # give recipients a chance of receiving patches in the correct order
        time.sleep(sleep)
    except Exception, err:
        raise CmdException, str(err)

    s.quit()

def __build_first(tmpl, total_nr, msg_id):
    """Build the first message (series description) to be sent via SMTP
    """
    headers_end = 'Message-Id: %s\n' % (msg_id)
    total_nr_str = str(total_nr)

    tmpl_dict = {'endofheaders': headers_end,
                 'date':         email.Utils.formatdate(localtime = True),
                 'totalnr':      total_nr_str}

    try:
        msg = tmpl % tmpl_dict
    except KeyError, err:
        raise CmdException, 'Unknown patch template variable: %s' \
              % err
    except TypeError:
        raise CmdException, 'Only "%(name)s" variables are ' \
              'supported in the patch template'

    return msg


def __build_message(tmpl, patch, patch_nr, total_nr, msg_id, ref_id = None):
    """Build the message to be sent via SMTP
    """
    p = crt_series.get_patch(patch)

    descr = p.get_description().strip()
    descr_lines = descr.split('\n')

    short_descr = descr_lines[0].rstrip()
    long_descr = reduce(lambda x, y: x + '\n' + y,
                        descr_lines[1:], '').lstrip()

    headers_end = 'Message-Id: %s\n' % (msg_id)
    if ref_id:
        headers_end += "In-Reply-To: %s\n" % (ref_id)
        headers_end += "References: %s\n" % (ref_id)

    total_nr_str = str(total_nr)
    patch_nr_str = str(patch_nr).zfill(len(total_nr_str))

    tmpl_dict = {'patch':        patch,
                 'shortdescr':   short_descr,
                 'longdescr':    long_descr,
                 'endofheaders': headers_end,
                 'diff':         git.diff(rev1 = git_id('%s/bottom' % patch),
                                          rev2 = git_id('%s/top' % patch)),
                 'diffstat':     git.diffstat(rev1 = git_id('%s/bottom'%patch),
                                              rev2 = git_id('%s/top' % patch)),
                 'date':         email.Utils.formatdate(localtime = True),
                 'patchnr':      patch_nr_str,
                 'totalnr':      total_nr_str,
                 'authname':     p.get_authname(),
                 'authemail':    p.get_authemail(),
                 'authdate':     p.get_authdate(),
                 'commname':     p.get_commname(),
                 'commemail':    p.get_commemail()}
    for key in tmpl_dict:
        if not tmpl_dict[key]:
            tmpl_dict[key] = ''

    try:
        msg = tmpl % tmpl_dict
    except KeyError, err:
        raise CmdException, 'Unknown patch template variable: %s' \
              % err
    except TypeError:
        raise CmdException, 'Only "%(name)s" variables are ' \
              'supported in the patch template'

    return msg


def func(parser, options, args):
    """Send the patches by e-mail using the patchmail.tmpl file as
    a template
    """
    if len(args) > 1:
        parser.error('incorrect number of arguments')

    if not config.has_option('stgit', 'smtpserver'):
        raise CmdException, 'smtpserver not defined'
    smtpserver = config.get('stgit', 'smtpserver')

    smtpuser = None
    smtppassword = None
    if config.has_option('stgit', 'smtpuser'):
        smtpuser = config.get('stgit', 'smtpuser')
    if config.has_option('stgit', 'smtppassword'):
        smtppassword = config.get('stgit', 'smtppassword')

    applied = crt_series.get_applied()

    if len(args) == 1:
        if args[0] in applied:
            patches = [args[0]]
        else:
            raise CmdException, 'Patch "%s" not applied' % args[0]
    elif options.all:
        patches = applied
    elif options.range:
        boundaries = options.range.split(':')
        if len(boundaries) == 1:
            start = boundaries[0]
            stop = boundaries[0]
        elif len(boundaries) == 2:
            if boundaries[0] == '':
                start = applied[0]
            else:
                start = boundaries[0]
            if boundaries[1] == '':
                stop = applied[-1]
            else:
                stop = boundaries[1]
        else:
            raise CmdException, 'incorrect parameters to "--range"'

        if start in applied:
            start_idx = applied.index(start)
        else:
            raise CmdException, 'Patch "%s" not applied' % start
        if stop in applied:
            stop_idx = applied.index(stop) + 1
        else:
            raise CmdException, 'Patch "%s" not applied' % stop

        if start_idx >= stop_idx:
            raise CmdException, 'Incorrect patch range order'

        patches = applied[start_idx:stop_idx]
    else:
        raise CmdException, 'Incorrect options. Unknown patches to send'

    if options.smtp_password:
        smtppassword = options.smtp_password

    if options.smtp_user:
        smtpuser = options.smtp_user

    if (smtppassword and not smtpuser):
        raise CmdException, 'SMTP password supplied, username needed'
    if (smtpuser and not smtppassword):
        raise CmdException, 'SMTP username supplied, password needed'

    total_nr = len(patches)
    if total_nr == 0:
        raise CmdException, 'No patches to send'

    ref_id = options.refid

    if options.sleep != None:
        sleep = options.sleep
    else:
        sleep = 2

    # send the first message (if any)
    if options.first:
        tmpl = file(options.first).read()
        from_addr, to_addr_list = __parse_addresses(tmpl)

        msg_id = email.Utils.make_msgid('stgit')
        msg = __build_first(tmpl, total_nr, msg_id)

        # subsequent e-mails are seen as replies to the first one
        ref_id = msg_id

        print 'Sending file "%s"...' % options.first,
        sys.stdout.flush()

        __send_message(smtpserver, from_addr, to_addr_list, msg, sleep,
                       smtpuser, smtppassword)

        print 'done'

    # send the patches
    if options.template:
        tfile = options.template
    else:
        tfile = os.path.join(git.base_dir, 'patchmail.tmpl')
    tmpl = file(tfile).read()

    from_addr, to_addr_list = __parse_addresses(tmpl)

    for (p, patch_nr) in zip(patches, range(1, len(patches) + 1)):
        msg_id = email.Utils.make_msgid('stgit')
        msg = __build_message(tmpl, p, patch_nr, total_nr, msg_id, ref_id)
        # subsequent e-mails are seen as replies to the first one
        if not ref_id:
            ref_id = msg_id

        print 'Sending patch "%s"...' % p,
        sys.stdout.flush()

        __send_message(smtpserver, from_addr, to_addr_list, msg, sleep,
                       smtpuser, smtppassword)

        print 'done'