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
import os.path
import urlparse
import types
import urllib
import glob
import rpm
import re

import rpmUtils.transaction
import rpmUtils.arch
import Errors
import urlgrabber
import urlgrabber.grabber
from repos import variableReplace
import repos


class CFParser(ConfigParser.ConfigParser):
    """wrapper around ConfigParser to provide two simple but useful functions:
       _getoption() and _getboolean()"""
    
    def _getoption(self, section, option, default=None):
        """section  - section of config
           option - option from section
           default - if there is no setting
           """
        try:
            return self.get(section, option)
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError), e:
            return default

    def _getboolean(self, section, option, default=None):
        """section  - section of config
           option - option from section
           default - if there is no setting
           """
        try:
            return self.getboolean(section, option)
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError), e:            
            return default

    def _getint(self, section, option, default=None):
        """section  - section of config
           option - option from section
           default - if there is no setting
           """
        try:
            return self.getint(section, option)
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError), e:            
            return default
    
    def _getfloat(self, section, option, default=None):
        """section  - section of config
           option - option from section
           default - if there is no setting
           """
        try:
            return self.getfloat(section, option)
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError), e:            
            return default

    def getbytes(self, section, option, default=None):
        """Get a friendly bytes/bandwidth option as bytes. 

        See _parsebytes() method for valid option values.
        """
        try:
             return self._parsebytes(self.get(section, option))
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError), e:
            return default

    def getthrottle(self, section, option, default=None):
        """Get a throttle option. 

        Input may either be a percentage or a "friendly bandwidth value" as
        accepted by the _parsebytes() method.

        Valid inputs: 100, 50%, 80.5%, 123M, 45.6k, 12.4G, 100K, 786.0, 0
        Invalid inputs: 100.1%, -4%, -500

        Return value will be a int if a bandwidth value was specified or a
        float if a percentage was given.

        ValueError will be raised if input couldn't be parsed.
        """
        try:
             optval = self.get(section, option)
        except (ConfigParser.NoSectionError, ConfigParser.NoOptionError), e:
            return default

        if len(optval) < 1:
            raise ValueError("no value specified")

        if optval[-1] == '%':
            n = optval[:-1]
            try:
                n = float(n)
            except ValueError:
                raise ValueError("couldn't convert '%s' to number" % n)
            if n < 0 or n > 100:
                raise ValueError("percentage is out of range")
            return n / 100.0
        else:
            return self._parsebytes(optval)

    def _parsebytes(self, optval):
        """Parse a friendly bandwidth option to bytes

        The input should be a string containing a (possibly floating point)
        number followed by an optional single character unit. Valid units are
        'k', 'M', 'G'. Case is ignored.
       
        Valid inputs: 100, 123M, 45.6k, 12.4G, 100K, 786.3, 0
        Invalid inputs: -10, -0.1, 45.6L, 123Mb

        Return value will always be an integer

        1k = 1024 bytes.

        ValueError will be raised if the option couldn't be parsed.
        """
        MULTS = {
            'k': 1024,
            'm': 1024*1024,
            'g': 1024*1024*1024,
        }

        if len(optval) < 1:
            raise ValueError("no value specified")

        if optval[-1].isalpha():
            n = optval[:-1]
            unit = optval[-1].lower()
            mult = MULTS.get(unit, None)
            if not mult:
                raise ValueError("unknown unit '%s'" % unit)
        else:
            n = optval
            mult = 1
             
        try:
            n = float(n)
        except ValueError:
            raise ValueError("couldn't convert '%s' to number" % n)

        if n < 0:
            raise ValueError("bytes value may not be negative")

        return int(n * mult)


class yumconf(object):
    """primary config class for yum"""
    
    def __init__(self, configfile = '/etc/yum.conf', root='/'):
        self.cfg = CFParser()
        configh = confpp(configfile)
        try:
            self.cfg.readfp(configh)
        except ConfigParser.MissingSectionHeaderError, e:
            raise Errors.ConfigError,  'Error accessing config file: %s' % configfile
        except ConfigParser.ParsingError, e:
            raise Errors.ConfigError, str(e)
            
        self.repos = repos.RepoStorage() # class of repositories
        
        self.configdata = {} # dict to hold all the data goodies
       
        
        optionints = [('debuglevel', 2),
                      ('errorlevel', 2), 
                      ('retries', 10),
                      ('recent', 7)]
                      
                      
        #defaults -either get them or set them
        optionstrings = [('cachedir', '/var/cache/yum'), 
                         ('logfile', '/var/log/yum.log'), 
                         ('reposdir', '/etc/yum.repos.d'),
                         ('rss-filename', 'yum-rss.xml'),
                         ('pkgpolicy', 'newest'),
                         ('syslog_ident', None),
                         ('syslog_facility', 'LOG_USER'),
                         ('distroverpkg', 'fedora-release'),
                         ('installroot', root),
                         ('commands', []),
                         ('exclude', []),
                         ('failovermethod', 'roundrobin'),
                         ('yumversion', 'unversioned'),
                         ('proxy', None),
                         ('proxy_username', None),
                         ('proxy_password', None),
                         ('installonlypkgs', ['kernel', 'kernel-bigmem', 
                                              'kernel-enterprise','kernel-smp',
                                              'kernel-debug', 'kernel-unsupported', 
                                              'kernel-source', 'kernel-devel']),
                         ('kernelpkgnames', ['kernel','kernel-smp',
                                             'kernel-enterprise', 'kernel-bigmem',
                                             'kernel-BOOT']),
                         ('tsflags', [])]
                         
        optionbools = [('assumeyes', 0),
                       ('exactarch', 1),
                       ('tolerant', 1),
                       ('diskspacecheck', 1),
                       ('overwrite_groups', 0),
                       ('keepalive', 1),
                       ('gpgcheck', 0),
                       ('obsoletes', 0),
                       ('showdupesfromrepos', 0)]

        # not being set from the config file
        # or things that can't be handled like all the rest
        # - but should be in the config class
        optionothers = [('uid', 0),
                        ('cache', 0),
                        ('progess_obj', None)]

        optionfloats = [('timeout', 30.0)]


        # do the ints
        for (option, default) in optionints:
            value =  self.cfg._getint('main', option, default)
            self.configdata[option] = value
            setattr(self, option, value)

        # do the strings        
        for (option, default) in optionstrings:
            value =  self.cfg._getoption('main', option, default)
            self.configdata[option] = value
            setattr(self, option, value)
            
        # do the bools
        for (option, default) in optionbools:
            value = self.cfg._getboolean('main', option, default)
            self.configdata[option] = value
            setattr(self, option, value)
            
        # do the others            
        for (option, value) in optionothers:
            self.configdata[option] = value
            setattr(self, option, value)

        # do the floats
        for (option, value) in optionfloats:
            value = self.cfg._getfloat('main', option, default)
            self.configdata[option] = value
            setattr(self, option, value)
            
            
        # do the dirs - set the root if there is one (grumble)
        for option in ['cachedir', 'logfile']:
            path = self.configdata[option]
            root = self.configdata['installroot']
            rootedpath = root + path
            self.configdata[option] = rootedpath
            setattr(self, option, rootedpath)
        
        # bandwidth limit options require special parsing
        for option, getfunc in (('bandwidth', self.cfg.getbytes), 
                                ('throttle', self.cfg.getthrottle)):
            value = getfunc('main', option, 0)
            self.configdata[option] = value
            setattr(self, option, value)

        
        # and push our process object around a bit to things beneath us
        self.repos.progress = self.getConfigOption('progress_obj')
        
        # get our variables parsed
        self.yumvar = self._getEnvVar()
        self.yumvar['basearch'] = rpmUtils.arch.getBaseArch() # FIXME make this configurable??
        self.yumvar['arch'] = rpmUtils.arch.getCanonArch() # FIXME make this configurable??
        # figure out what the releasever really is from the distroverpkg
        self.yumvar['releasever'] = self._getsysver()
        
        # weird ones
        for option in ['commands', 'installonlypkgs', 'kernelpkgnames', 'exclude']:
            self.configdata[option] = variableReplace(self.yumvar, self.configdata[option])
            self.configdata[option] = variableReplace(self.yumvar, self.configdata[option])

        # make our lists into lists. :)
        for option in ['exclude', 'installonlypkgs', 'kernelpkgnames', 'tsflags']:
            self.configdata[option] = parseList(self.configdata[option])

        # look through our repositories.
        
        for section in self.cfg.sections(): # loop through the list of sections
            if section != 'main': # must be a repoid
                doRepoSection(self, self.cfg, section)

        # should read through self.getConfigOption('reposdir') for *.repo
        # does not read recursively
        # read each of them in using confpp, then parse them same as any other repo
        # section - as above.
        reposdir = self.getConfigOption('reposdir')
        if os.path.exists(self.getConfigOption('installroot') + '/' + reposdir):
            reposdir = self.getConfigOption('installroot') + '/' + reposdir
        
        reposglob = reposdir + '/*.repo'
        if os.path.exists(reposdir) and os.path.isdir(reposdir):
            repofn = glob.glob(reposglob)
            repofn.sort()
            
            for fn in repofn:
                if not os.path.isfile(fn):
                    continue
                try:
                    self._doFileRepo(fn)
                except Errors.ConfigError, e:
                    print >> sys.stderr, e
                    continue
                except Errors.RepoError, e:
                    print e
                    

    def listConfigOptions(self):
        """return list of options available for global config"""
        return self.configdata.keys()
        
    def setConfigOption(self, option, value):
        """option, value to set for global config options"""
        try:
            self.configdata[option] = value
            setattr(self, option, value)
        except KeyError:
            raise Errors.ConfigError, 'No such option %s' % option


    def getConfigOption(self, option, default=None):
        """gets global config setting, takes optional default value"""
        try:
            return self.configdata[option]
        except KeyError:
            return default

    def _getsysver(self):
        ts = rpmUtils.transaction.initReadOnlyTransaction(root=self.getConfigOption('installroot', '/'))
        ts.pushVSFlags(~(rpm._RPMVSF_NOSIGNATURES|rpm._RPMVSF_NODIGESTS))
        idx = ts.dbMatch('provides', self.getConfigOption('distroverpkg'))
        # we're going to take the first one - if there is more than one of these
        # then the user needs a beating
        if idx.count() == 0:
            releasever = 'Null'
        else:
            hdr = idx.next()
            releasever = hdr['version']
            del hdr
        del idx
        del ts
        return releasever
    
    def _getEnvVar(self):
        yumvar = {}
        for num in range(0, 10):
            env='YUM%s' % num
            var='$%s' % env
            yumvar[num] = os.environ.get(env, var)
        
        return yumvar

    def _doFileRepo(self, fn):
        """takes a filename of a repo config section and fills up the repo data
           from it"""
           
        repoparsed = confpp(fn)
        repoconf = CFParser()
        try:
            repoconf.readfp(repoparsed)
        except ConfigParser.MissingSectionHeaderError, e:
            raise Errors.ConfigError, 'Error: Bad repository file %s. Skipping' % fn
        
        for section in repoconf.sections():
            if section != 'main': # check for morons
                # this sucks but show me a nice way of doing this.
                doRepoSection(self, repoconf, section)

def doRepoSection(globconfig, thisconfig, section):
    """do all the repo handling stuff for this config"""
    
    
    thisrepo = globconfig.repos.add(section)
    thisrepo.set('yumvar', globconfig.yumvar)
    
    enabled = thisconfig._getboolean(section, 'enabled', 1)
    name = thisconfig._getoption(section, 'name', section)
    name = variableReplace(globconfig.yumvar, name)
    thisrepo.set('name', name) 
    thisrepo.set('enabled', enabled)
    
    baseurl = thisconfig._getoption(section, 'baseurl', [])
    baseurls = parseList(baseurl)
    mirrorlistfn = thisconfig._getoption(section, 'mirrorlist', None)
    mirrorlistfn = variableReplace(globconfig.yumvar, mirrorlistfn)
    thisrepo.set('mirrorlistfn', mirrorlistfn)
    thisrepo.set('baseurls', baseurls)
    
    gpgkey = thisconfig._getoption(section, 'gpgkey', '')
    if gpgkey:
        (s,b,p,q,f,o) = urlparse.urlparse(gpgkey)
        if s not in ('http', 'ftp', 'file', 'https'):
            print 'gpgkey must be ftp, http[s], or file URL, ignoring - %s' % gpgkey
            gpgkey = ''
    thisrepo.set('gpgkey', gpgkey)


    for keyword in ['proxy_username', 'proxy', 'proxy_password', 
                    'retries', 'failovermethod']:

        thisrepo.set(keyword, thisconfig._getoption(section, keyword, \
                     globconfig.getConfigOption(keyword)))
                     
    for keyword, getfunc in (('bandwidth', thisconfig.getbytes),
                             ('throttle', thisconfig.getthrottle)):
        thisrepo.set(keyword, getfunc(section, keyword, 
                    globconfig.getConfigOption(keyword)))

    for keyword in ['gpgcheck', 'keepalive']:
        thisrepo.set(keyword, thisconfig._getboolean(section, \
                     keyword, globconfig.getConfigOption(keyword)))
    
    for keyword in ['timeout']:
        thisrepo.set(keyword, thisconfig._getfloat(section, \
                     keyword, globconfig.getConfigOption(keyword)))
    
    excludelist = thisconfig._getoption(section, 'exclude', [])
    excludelist = variableReplace(globconfig.yumvar, excludelist)
    excludelist = parseList(excludelist)
    thisrepo.set('excludes', excludelist)

    includelist = thisconfig._getoption(section, 'includepkgs', [])
    includelist = variableReplace(globconfig.yumvar, includelist)
    includelist = parseList(includelist)
    thisrepo.set('includepkgs', includelist)

    thisrepo.set('enablegroups', thisconfig._getboolean(section, 'enablegroups', 1))
    
    cachedir = os.path.join(globconfig.getConfigOption('cachedir'), section)
    pkgdir = os.path.join(cachedir, 'packages')
    hdrdir = os.path.join(cachedir, 'headers')
    thisrepo.set('cachedir', cachedir)
    thisrepo.set('pkgdir', pkgdir)
    thisrepo.set('hdrdir', hdrdir)
    


def parseList(value):
    """converts strings from a configparser option into a workable list
       converts commas and spaces to separators for the list"""
       
    if type(value) is types.ListType:
        return value
        
    listvalue = []
    # we need to allow for the '\n[whitespace]' continuation - easier
    # to sub the \n with a space and then read the lines
    slashnrepl = re.compile('\n')
    commarepl = re.compile(',')
    (value, count) = slashnrepl.subn(' ', value)
    (value, count) = commarepl.subn(' ', value)
    listvalue = value.split()
    return listvalue
        


class confpp:
    """
    ConfigParser Include Pre-Processor
    
        File-like Object capable of pre-processing include= lines for
        a ConfigParser. 
        
        The readline function expands lines matching include=(url)
        into lines from the url specified. Includes may occur in
        included files as well. 
        
        Suggested Usage:
            cfg = ConfigParser.ConfigParser()
            fileobj = confpp( fileorurl )
            cfg.readfp(fileobj)
    """
    
    
    def __init__(self, configfile):
        # set some file-like object attributes for ConfigParser
        # these just make confpp look more like a real file object.
        self.mode = 'r' 
        
        # establish whether to use urlgrabber or urllib
        # we want to use urlgrabber if it supports urlopen
        if hasattr(urlgrabber.grabber, 'urlopen'):
            self._urlresolver = urlgrabber.grabber
        else: 
            self._urlresolver = urllib
        
        
        # first make configfile a url even if it points to 
        # a local file
        scheme = urlparse.urlparse(configfile)[0]
        if scheme == '':
            # check it to make sure it's not a relative file url
            if configfile[0] != '/':
                configfile = os.getcwd() + '/' + configfile
            url = 'file://' + configfile
        else:
            url = configfile
        
        # these are used to maintain the include stack and check
        # for recursive/duplicate includes
        self._incstack = []
        self._alreadyincluded = []
        
        # _pushfile will return None if he couldn't open the file
        fo = self._pushfile( url )
        if fo is None: 
            raise Errors.ConfigError, 'Error accessing file: %s' % url
        
    def readline( self, size=0 ):
        """
        Implementation of File-Like Object readline function. This should be
        the only function called by ConfigParser according to the python docs.
        We maintain a stack of real FLOs and delegate readline calls to the 
        FLO on top of the stack. When EOF occurs on the topmost FLO, it is 
        popped off the stack and the next FLO takes over. include= lines 
        found anywhere cause a new FLO to be opened and pushed onto the top 
        of the stack. Finally, we return EOF when the bottom-most (configfile
        arg to __init__) FLO returns EOF.
        
        Very Technical Pseudo Code:
        
        def confpp.readline() [this is called by ConfigParser]
            open configfile, push on stack
            while stack has some stuff on it
                line = readline from file on top of stack
                pop and continue if line is EOF
                if line starts with 'include=' then
                    error if file is recursive or duplicate
                    otherwise open file, push on stack
                    continue
                else
                    return line
            
            return EOF
        """
        
        # set line to EOF initially. 
        line=''
        while len(self._incstack) > 0:
            # peek at the file like object on top of the stack
            fo = self._incstack[-1]
            line = fo.readline()
            if len(line) > 0:
                m = re.match( r'\s*include\s*=\s*(?P<url>.*)', line )
                if m:
                    url = m.group('url')
                    if len(url) == 0:
                        raise Errors.ConfigError, \
                             'Error parsing config %s: include must specify file to include.' % (self.name)
                    else:
                        # whooohoo a valid include line.. push it on the stack
                        fo = self._pushfile( url )
                else:
                    # line didn't match include=, just return it as is
                    # for the ConfigParser
                    break
            else:
                # the current file returned EOF, pop it off the stack.
                self._popfile()
        
        # at this point we have a line from the topmost file on the stack
        # or EOF if the stack is empty
        return line
    
    
    def _absurl( self, url ):
        """
        Returns an absolute url for the (possibly) relative
        url specified. The base url used to resolve the
        missing bits of url is the url of the file currently
        being included (i.e. the top of the stack).
        """
        
        if len(self._incstack) == 0:
            # it's the initial config file. No base url to resolve against.
            return url
        else:
            return urlparse.urljoin( self.geturl(), url )
    
    
    def _pushfile( self, url ):
        """
        Opens the url specified, pushes it on the stack, and 
        returns a file like object. Returns None if the url 
        has previously been included.
        If the file can not be opened this function exits.
        """
        
        # absolutize this url using the including files url
        # as a base url.
        absurl = self._absurl(url)
        # check if this has previously been included.
        if self._urlalreadyincluded(absurl):
            return None
        try:
            fo = self._urlresolver.urlopen(absurl)
        except urlgrabber.grabber.URLGrabError, e:
            fo = None
        if fo is not None:
            self.name = absurl
            self._incstack.append( fo )
            self._alreadyincluded.append(absurl)
        else:
            raise Errors.ConfigError, \
                  'Error accessing file for config %s' % (absurl)

        return fo
    
    
    def _popfile( self ):
        """
        Pop a file off the stack signaling completion of including that file.
        """
        fo = self._incstack.pop()
        fo.close()
        if len(self._incstack) > 0:
            self.name = self._incstack[-1].geturl()
        else:
            self.name = None
    
    
    def _urlalreadyincluded( self, url ):
        """
        Checks if the url has already been included at all.. this 
        does not necessarily have to be recursive
        """
        for eurl in self._alreadyincluded:
            if eurl == url: return 1
        return 0
    
    
    def geturl(self): return self.name

def main(args):
    myfile = args[0]
    if len(args) > 1:
        if args[1] == '--dump':
            configh = confpp(myfile)        
            while 1:
                line = configh.readline()
                if not line: break
                print line,
            sys.exit(0)

    conf = yumconf(configfile = myfile)                


    for option in conf.listConfigOptions():
        print '%s = %s' % (option, conf.getConfigOption(option))
        
    print '\n\n'
    repositories = conf.repos
    repolist = repositories.sort()
    
    for repo in repolist:
        print repo.dump()
            
        print ''
    
    

if __name__ == "__main__":
        if len(sys.argv) < 2:
            print 'command: config file'
            sys.exit(1)
            
        main(sys.argv[1:])
