#!/usr/bin/python -t
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
# Copyright 2002 Duke University 

import ConfigParser
import sys
import os
import urlparse
import string
import urllib

from i18n import _


class yumconf:

    def __init__(self, configfile = '/etc/yum.conf'):
        self.cfg = ConfigParser.ConfigParser()
        (s,b,p,q,f,o) = urlparse.urlparse(configfile)
        if s in ('http', 'ftp','file'):
            configfh = urllib.urlopen(configfile)
            try:
                self.cfg.readfp(configfh)
            except ConfigParser.MissingSectionHeaderError, e:
                print _('Error accessing URL: %s') % configfile
                sys.exit(1)
        else:
            if os.access(configfile, os.R_OK):
                self.cfg.read(configfile)
            else:
                print _('Error accessing File: %s') % configfile
                sys.exit(1)
        self.servers = []
        self.servername = {}
        self.serverurl = {}
        self.serverpkgdir = {}
        self.serverhdrdir = {}
        self.servercache = {}
        self.servergpgcheck= {}
        self.excludes= []
        
        #defaults
        self.cachedir = '/var/cache/yum'
        self.debuglevel = 2
        self.logfile = '/var/log/yum.log'
        self.pkgpolicy = 'newest'
        #self.gpghome = '/root/.gnupg'
        #self.gpgkeyring = None
        self.assumeyes = 0
        self.errorlevel = 2
        self.cache = 0
        self.uid = 0
        self.commands = None
        self.exactarch = 0
        
        if self._getoption('main','cachedir') != None:
            self.cachedir=self._getoption('main','cachedir')
        if self._getoption('main','debuglevel') != None:
            self.debuglevel=self._getoption('main','debuglevel')
        if self._getoption('main','logfile') != None:
            self.logfile=self._getoption('main','logfile')
        if self._getoption('main','pkgpolicy') != None:
            self.pkgpolicy=self._getoption('main','pkgpolicy')
        if self._getoption('main','exclude') != None:
            self.excludes=string.split(self._getoption('main','exclude'), ' ')
        if self._getoption('main','assumeyes') != None:
            self.assumeyes=self.cfg.getboolean('main', 'assumeyes')
        if self._getoption('main','errorlevel') != None:
            self.errorlevel=self._getoption('main', 'errorlevel')
        #if self._getoption('main','gpghome') != None:
        #    self.gpghome=self._getoption('main', 'gpghome')
        #if self._getoption('main','gpgkeyring') != None:
        #    self.gpgkeyring=self._getoption('main', 'gpgkeyring')
        if self._getoption('main','commands') != None:
            self.commands=string.split(self._getoption('main', 'commands'),' ')
        if self._getoption('main','exactarch') != None:
            self.exactarch=self.cfg.getboolean('main', 'exactarch')

        if len(self.cfg.sections()) > 1:
            for section in self.cfg.sections(): # loop through the list of sections
                if section != 'main': # must be a serverid
                    name = self._getoption(section,'name')
                    url = self._getoption(section,'baseurl')
                    if name != None and url != None:
                        self.servers.append(section)
                        self.servername[section] = name
                        self.serverurl[section] = url
                        if self._getoption(section,'gpgcheck') != None:
                            self.servergpgcheck[section]=self.cfg.getboolean(section,'gpgcheck')
                        else:
                            self.servergpgcheck[section]=0
                        
                        (s,b,p,q,f,o) = urlparse.urlparse(self.serverurl[section])
                        # currently only allowing http, ftp and file url types
                        if s not in ['http', 'ftp', 'file']:
                            print _('Not using ftp, http or file for servers, Aborting - %s') % (self.serverurl[section])
                            sys.exit(1)
                        cache = os.path.join(self.cachedir,section)
                        pkgdir = os.path.join(cache, 'packages')
                        hdrdir = os.path.join(cache, 'headers')
                        self.servercache[section] = cache
                        self.serverpkgdir[section] = pkgdir
                        self.serverhdrdir[section] = hdrdir
                    else:
                        print _('Error: Cannot find baseurl or name for server \'%s\'. Skipping') %(section)    
        else:
            print _('Insufficient server config - no servers found. Aborting.')
            sys.exit(1)

    def _getoption(self, section, option):
        try:
            return self.cfg.get(section, option)
        except ConfigParser.NoSectionError, e:
            print _('Failed to find section: %s') % section
        except ConfigParser.NoOptionError, e:
            return None
        
   
