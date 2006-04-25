#!/usr/bin/python -tt
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
# Copyright 2005 Duke University


import os
import sys
import os.path
import rpm
import re
import fnmatch
import types
import errno
import time
import sre_constants
import glob


import Errors
import rpmUtils
import rpmUtils.updates
import rpmUtils.arch
import comps
import config
import parser
import repos
import misc
import transactioninfo
from urlgrabber.grabber import URLGrabError
import depsolve
import plugins


from packages import parsePackages, YumAvailablePackage, YumLocalPackage, YumInstalledPackage
from repomd import mdErrors
from constants import *
from repomd.packageSack import ListPackageSack

__version__ = '2.6.0'

class YumBase(depsolve.Depsolve):
    """This is a primary structure and base class. It houses the objects and
       methods needed to perform most things in yum. It is almost an abstract
       class in that you will need to add your own class above it for most
       real use."""
    
    def __init__(self):
        depsolve.Depsolve.__init__(self)
        self.localdbimported = 0
        self.repos = repos.RepoStorage() # class of repositories
        if (not self.repos.sqlite):
            self.log(1,"Warning, could not load sqlite, falling back to pickle")

        # Start with plugins disabled
        self.disablePlugins()

        self.localPackages = [] # for local package handling 

    def _transactionDataFactory(self):
        """Factory method returning TransactionData object"""
        if self.conf.enable_group_conditionals:
            return transactioninfo.ConditionalTransactionData()
        return transactioninfo.TransactionData()

    def log(self, value, msg):
        """dummy log stub"""
        print msg

    def errorlog(self, value, msg):
        """dummy errorlog stub"""
        print >> sys.stderr, msg

    def filelog(self, value, msg):
        print msg
   
    def doGenericSetup(self, cache=0):
        """do a default setup for all the normal/necessary yum components,
           really just a shorthand for testing"""
        
        self.doConfigSetup()
        self.conf.cache = cache
        self.doTsSetup()
        self.doRpmDBSetup()
        self.doRepoSetup()
        self.doSackSetup()
        
    def doConfigSetup(self, fn='/etc/yum.conf', root='/'):
        """basic stub function for doing configuration setup"""
       
        self.conf = config.readMainConfig(fn, root)
        self.yumvar = self.conf.yumvar
        self.getReposFromConfig()

    def getReposFromConfig(self):
        """read in repositories from config main and .repo files"""

        reposlist = []

        # Check yum.conf for repositories
        for section in self.conf.cfg.sections():
            # All sections except [main] are repositories
            if section == 'main': 
                continue

            try:
                thisrepo = config.readRepoConfig(self.conf.cfg, section, self.conf)
            except (Errors.RepoError, Errors.ConfigError), e:
                self.errorlog(2, e)
            else:
                reposlist.append(thisrepo)

        # Read .repo files from directories specified by the reposdir option
        # (typically /etc/yum.repos.d and /etc/yum/repos.d)
        parser = config.IncludedDirConfigParser(vars=self.yumvar)
        for reposdir in self.conf.reposdir:
            if os.path.exists(self.conf.installroot+'/'+reposdir):
                reposdir = self.conf.installroot + '/' + reposdir

            if os.path.isdir(reposdir):
                #XXX: why can't we just pass the list of files?
                files = ' '.join(glob.glob('%s/*.repo' % reposdir))
                #XXX: error catching here
                parser.read(files)

        # Check sections in the .repo files that were just slurped up
        for section in parser.sections():
            try:
                thisrepo = config.readRepoConfig(parser, section, self.conf)
            except (Errors.RepoError, Errors.ConfigError), e:
                self.errorlog(2, e)
            else:
                reposlist.append(thisrepo)

        # Got our list of repo objects, add them to the repos collection
        for thisrepo in reposlist:
            try:
                self.repos.add(thisrepo)
            except Errors.RepoError, e: 
                self.errorlog(2, e)
                continue

    def disablePlugins(self):
        '''Disable yum plugins
        '''
        self.plugins = plugins.DummyYumPlugins()
    
    def doPluginSetup(self, optparser=None, types=None):
        '''Initialise and enable yum plugins. 

        If plugins are going to be used, this should be called soon after
        doConfigSetup() has been called.

        @param optparser: The OptionParser instance for this run (optional)
        @param types: A sequence specifying the types of plugins to load.
            This should be sequnce containing one or more of the
            yum.plugins.TYPE_...  constants. If None (the default), all plugins
            will be loaded.
        '''
        # Load plugins first as they make affect available config options
        self.plugins = plugins.YumPlugins(self, self.conf.pluginpath,
                optparser, types)

        # Process options registered by plugins
        self.plugins.parseopts(self.conf, self.repos.findRepos('*'))

        # Initialise plugins
        self.plugins.run('init')

    def doTsSetup(self):
        """setup all the transaction set storage items we'll need
           This can't happen in __init__ b/c we don't know our installroot
           yet"""
        
        if hasattr(self, 'read_ts'):
            return
            
        if not self.conf.installroot:
            raise Errors.YumBaseError, 'Setting up TransactionSets before config class is up'
        
        installroot = self.conf.installroot
        self.read_ts = rpmUtils.transaction.initReadOnlyTransaction(root=installroot)
        self.tsInfo = self._transactionDataFactory()
        self.rpmdb = rpmUtils.RpmDBHolder()
        self.initActionTs()
        
    def doRpmDBSetup(self):
        """sets up a holder object for important information from the rpmdb"""
        
        if not self.localdbimported:
            self.log(3, 'Reading Local RPMDB')
            self.rpmdb.addDB(self.read_ts)
            self.localdbimported = 1

    def closeRpmDB(self):
        """closes down the instances of the rpmdb we have wangling around"""
        if hasattr(self, 'rpmdb'):
            del self.rpmdb
            self.localdbimported = 0
        if hasattr(self, 'ts'):
            del self.ts.ts
            del self.ts
        if hasattr(self, 'read_ts'):
            del self.read_ts.ts
            del self.read_ts
        if hasattr(self, 'up'):
            del self.up
        if hasattr(self, 'comps'):
            self.comps.compiled = False
            

    def doRepoSetup(self, thisrepo=None):
        """grabs the repomd.xml for each enabled repository and sets up 
           the basics of the repository"""

        self.plugins.run('prereposetup')
        
        if thisrepo is None:
            repos = self.repos.listEnabled()
        else:
            repos = self.repos.findRepos(thisrepo)

        if len(repos) < 1:
            self.errorlog(0, 'No Repositories Available to Set Up')

        num = 1
        for repo in repos:
            if repo.repoXML is not None and len(repo.urls) > 0:
                num += 1
                continue
            if self.repos.callback:
                self.repos.callback.log(2, '%-68s [%d/%d]' % 
                                        (repo.id, num, len(repos)))
                
            try:
                repo.cache = self.conf.cache
                repo.baseurlSetup()
                repo.dirSetup()
                self.log(3, 'Baseurl(s) for repo: %s' % repo.urls)
            except Errors.RepoError, e:
                self.errorlog(0, '%s' % e)
                raise
                
            try:
                repo.getRepoXML(text=repo)
            except Errors.RepoError, e:
                self.errorlog(0, 'Cannot open/read repomd.xml file for repository: %s' % repo)
                self.errorlog(0, str(e))
                raise
            num += 1
            
            
        if self.repos.callback and len(repos) > 0:
            self.repos.callback.progressbar(num, len(repos), repo.id)
            
        self.plugins.run('postreposetup')

    def doSackSetup(self, archlist=None, thisrepo=None):
        """populates the package sacks for information from our repositories,
           takes optional archlist for archs to include"""
           
        if hasattr(self, 'pkgSack') and thisrepo is None:
            self.log(7, 'skipping reposetup, pkgsack exists')
            return
            
        if thisrepo is None:
            repos = self.repos.listEnabled()
        else:
            repos = self.repos.findRepos(thisrepo)
            
        self.log(3, 'Setting up Package Sacks')
        if not archlist:
            archlist = rpmUtils.arch.getArchList()

        archdict = {}
        for arch in archlist:
            archdict[arch] = 1

        self.repos.pkgSack.compatarchs = archdict
        self.repos.populateSack(which=repos)
        self.pkgSack = self.repos.pkgSack
        self.excludePackages()
        self.pkgSack.excludeArchs(archlist)

        for repo in repos:
            self.excludePackages(repo)
            self.includePackages(repo)
        self.plugins.run('exclude')
        self.pkgSack.buildIndexes()
        
    def doUpdateSetup(self):
        """setups up the update object in the base class and fills out the
           updates, obsoletes and others lists"""
        
        if hasattr(self, 'up'):
            return
            
        self.log(3, 'Building updates object')
        #FIXME - add checks for the other pkglists to see if we should
        # raise an error
        if not hasattr(self, 'pkgSack'):
            self.doRepoSetup()
            self.doSackSetup()
        
        self.up = rpmUtils.updates.Updates(self.rpmdb.getPkgList(),
                                           self.pkgSack.simplePkgList())
        if self.conf.debuglevel >= 6:
            self.up.debug = 1
            
        if self.conf.obsoletes:
            self.up.rawobsoletes = self.pkgSack.returnObsoletes()
            
        self.up.exactarch = self.conf.exactarch
        self.up.exactarchlist = self.conf.exactarchlist
        self.up.doUpdates()

        if self.conf.obsoletes:
            self.up.doObsoletes()

        self.up.condenseUpdates()
        
    
    def doGroupSetup(self):
        """create the groups object that will store the comps metadata
           finds the repos with groups, gets their comps data and merge it
           into the group object"""
        
        self.log(3, 'Getting group metadata')
        reposWithGroups = []
        for repo in self.repos.listGroupsEnabled():
            if repo.groups_added: # already added the groups from this repo
                reposWithGroups.append(repo)
                continue
                
            if repo.repoXML is None:
                raise Errors.RepoError, "Repository '%s' not yet setup" % repo
            try:
                groupremote = repo.repoXML.groupLocation()
            except mdErrors.RepoMDError, e:
                pass
            else:
                reposWithGroups.append(repo)
                
        # now we know which repos actually have groups files.
        overwrite = self.conf.overwrite_groups
        if not hasattr(self, 'comps'):
            self.comps = comps.Comps(overwrite_groups = overwrite)

        for repo in reposWithGroups:
            if repo.groups_added: # already added the groups from this repo
                continue
                
            self.log(4, 'Adding group file from repository: %s' % repo)
            groupfile = repo.getGroups()
            try:
                self.comps.add(groupfile)
            except Errors.GroupsError, e:
                self.errorlog(0, 'Failed to add groups file for repository: %s' % repo)
            else:
                repo.groups_added = True

        if self.comps.compscount == 0:
            raise Errors.GroupsError, 'No Groups Available in any repository'
        
        self.doRpmDBSetup()
        pkglist = self.rpmdb.getPkgList()
        self.comps.compile(pkglist)
        

    def doSackFilelistPopulate(self):
        """convenience function to populate the repos with the filelist metadata
           it also is simply to only emit a log if anything actually gets populated"""
        
        necessary = False
        for repo in self.repos.listEnabled():
            if 'filelists' in self.pkgSack.added[repo.id]:
                continue
            else:
                necessary = True
        
        if necessary:
            msg = 'Importing additional filelist information'
            self.log(2, msg)
            self.repos.populateSack(with='filelists')
           
    def buildTransaction(self):
        """go through the packages in the transaction set, find them in the
           packageSack or rpmdb, and pack up the ts accordingly"""
        self.plugins.run('preresolve')
        (rescode, restring) = self.resolveDeps()
        self.plugins.run('postresolve', rescode=rescode, restring=restring)

        if self.tsInfo.changed:
            (rescode, restring) = self.resolveDeps()
            
        return rescode, restring

    def runTransaction(self, cb):
        """takes an rpm callback object, performs the transaction"""

        self.plugins.run('pretrans')

        errors = self.ts.run(cb.callback, '')
        if errors:
            raise Errors.YumBaseError, errors

        if not self.conf.keepcache:
            self.cleanHeaders()
            self.cleanPackages()

        self.plugins.run('posttrans')
        
    def excludePackages(self, repo=None):
        """removes packages from packageSacks based on global exclude lists,
           command line excludes and per-repository excludes, takes optional 
           repo object to use."""
        
        # if not repo: then assume global excludes, only
        # if repo: then do only that repos' packages and excludes
        
        if not repo: # global only
            excludelist = self.conf.exclude
            repoid = None
        else:
            excludelist = repo.exclude
            repoid = repo.id

        if len(excludelist) == 0:
            return
        
        if not repo:
            self.log(2, 'Excluding Packages in global exclude list')
        else:
            self.log(2, 'Excluding Packages from %s' % repo.name)
            
        exactmatch, matched, unmatched = \
           parsePackages(self.pkgSack.returnPackages(repoid), excludelist, casematch=1)
        
        for po in exactmatch + matched:
            self.log(3, 'Excluding %s' % po)
            self.pkgSack.delPackage(po)
      
        self.log(2, 'Finished')

    def includePackages(self, repo):
        """removes packages from packageSacks based on list of packages, to include.
           takes repoid as a mandatory argument."""
        
        includelist = repo.includepkgs
        
        if len(includelist) == 0:
            return
        
        pkglist = self.pkgSack.returnPackages(repo.id)
        exactmatch, matched, unmatched = \
           parsePackages(pkglist, includelist, casematch=1)
        
        self.log(2, 'Reducing %s to included packages only' % repo.name)
        rmlist = []
        
        for po in pkglist:
            if po in exactmatch + matched:
                self.log(3, 'Keeping included package %s' % po)
                continue
            else:
                rmlist.append(po)
        
        for po in rmlist:
            self.log(3, 'Removing unmatched package %s' % po)
            self.pkgSack.delPackage(po)
            
        self.log(2, 'Finished')
        
    def doLock(self, lockfile):
        """perform the yum locking, raise yum-based exceptions, not OSErrors"""
        
        # if we're not root then we don't lock - just return nicely
        if self.conf.uid != 0:
            return
            
        root = self.conf.installroot
        lockfile = root + '/' + lockfile # lock in the chroot
        lockfile = os.path.normpath(lockfile) # get rid of silly preceding extra /
        
        mypid=str(os.getpid())    
        while not self._lock(lockfile, mypid, 0644):
            fd = open(lockfile, 'r')
            try: oldpid = int(fd.readline())
            except ValueError:
                # bogus data in the pid file. Throw away.
                self._unlock(lockfile)
            else:
                try: os.kill(oldpid, 0)
                except OSError, e:
                    if e[0] == errno.ESRCH:
                        # The pid doesn't exist
                        self._unlock(lockfile)
                    else:
                        # Whoa. What the heck happened?
                        msg = 'Unable to check if PID %s is active' % oldpid
                        raise Errors.LockError(1, msg)
                else:
                    # Another copy seems to be running.
                    msg = 'Existing lock %s: another copy is running. Aborting.' % lockfile
                    raise Errors.LockError(0, msg)
    
    def doUnlock(self, lockfile):
        """do the unlock for yum"""
        
        # if we're not root then we don't lock - just return nicely
        if self.conf.uid != 0:
            return
        
        root = self.conf.installroot
        lockfile = root + '/' + lockfile # lock in the chroot
        
        self._unlock(lockfile)
        
    def _lock(self, filename, contents='', mode=0777):
        lockdir = os.path.dirname(filename)
        try:
            if not os.path.exists(lockdir):
                os.makedirs(lockdir, mode=0755)
            fd = os.open(filename, os.O_EXCL|os.O_CREAT|os.O_WRONLY, mode)    
        except OSError, msg:
            if not msg.errno == errno.EEXIST: raise msg
            return 0
        else:
            os.write(fd, contents)
            os.close(fd)
            return 1
    
    def _unlock(self, filename):
        try:
            os.unlink(filename)
        except OSError, msg:
            pass


    def verifyPkg(self, fo, po, raiseError):
        """verifies the package is what we expect it to be
           raiseError  = defaults to 0 - if 1 then will raise
           a URLGrabError if the file does not check out.
           otherwise it returns false for a failure, true for success"""

        if type(fo) is types.InstanceType:
            fo = fo.filename

        for (csumtype, csum, csumid) in po.checksums:
            if csumid:
                checksum = csum
                checksumType = csumtype
                break
        try:
            self.verifyChecksum(fo, checksumType, checksum)
        except URLGrabError, e:
            if raiseError:
                raise
            else:
                return 0

        ylp = YumLocalPackage(self.read_ts, fo)
        if ylp.pkgtup != po.pkgtup:
            if raiseError:
                raise URLGrabError(-1, 'Package does not match intended download')
            else:
                return 0
        
        return 1
        
        
    def verifyChecksum(self, fo, checksumType, csum):
        """Verify the checksum of the file versus the 
           provided checksum"""

        try:
            filesum = misc.checksum(checksumType, fo)
        except Errors.MiscError, e:
            raise URLGrabError(-3, 'Could not perform checksum')
            
        if filesum != csum:
            raise URLGrabError(-1, 'Package does not match checksum')
        
        return 0
            
           
    def downloadPkgs(self, pkglist, callback=None):
        """download list of package objects handed to you, output based on
           callback, raise yum.Errors.YumBaseError on problems"""

        errors = {}
        self.plugins.run('predownload', pkglist=pkglist)
        repo_cached = False
        remote_pkgs = []
        for po in pkglist:
            if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
                continue
                    
            local = po.localPkg()
            if os.path.exists(local):
                cursize = os.stat(local)[6]
                totsize = int(po.size())
                try:
                    result = self.verifyPkg(local, po, raiseError=1)
                except URLGrabError, e: # fails the check
                    
                    repo = self.repos.getRepo(po.repoid)
                    if repo.cache:
                        repo_cached = True
                        msg = 'package fails checksum but caching is enabled for %s' % repo.id
                        if not errors.has_key(po): errors[po] = []
                        errors[po].append(msg)
                        
                    if cursize >= totsize: # keep it around for regetting
                        os.unlink(local)
                        
                else:
                    if result:
                        continue
                    else:
                        if cursize >= totsize: # keep it around for regetting
                            os.unlink(local)
            remote_pkgs.append(po)
            
            # caching is enabled and the package 
            # just failed to check out there's no 
            # way to save this, report the error and return
            if (self.conf.cache or repo_cached) and errors:
                return errors
                

        i = 0
        for po in remote_pkgs:
            i += 1
            repo = self.repos.getRepo(po.repoid)
            remote = po.returnSimple('relativepath')
            checkfunc = (self.verifyPkg, (po, 1), {})


            # FIXME - add check here to make sure we have the disk space
            # available to download the package. If we don't then politely
            # bail out with an informative message.
            # os.statvfs(repo's local path)[4]*[2] >= po.size
            try:
                text = '(%s/%s): %s' % (i, len(remote_pkgs),
                                        os.path.basename(remote))
                local = po.localPkg()
                mylocal = repo.get(relative=remote,
                                   local=local,
                                   checkfunc=checkfunc,
                                   text=text,
                                   cache=repo.http_caching != 'none',
                                   )
            except Errors.RepoError, e:
                if not errors.has_key(po):
                    errors[po] = []
                errors[po].append(str(e))
            else:
                po.localpath = mylocal
                if errors.has_key(po):
                    del errors[po]

        self.plugins.run('postdownload', pkglist=pkglist, errors=errors)

        return errors

    def verifyHeader(self, fo, po, raiseError):
        """check the header out via it's naevr, internally"""
        if type(fo) is types.InstanceType:
            fo = fo.filename
            
        try:
            hlist = rpm.readHeaderListFromFile(fo)
            hdr = hlist[0]
        except (rpm.error, IndexError):
            if raiseError:
                raise URLGrabError(-1, 'Header is not complete.')
            else:
                return 0
                
        yip = YumInstalledPackage(hdr) # we're using YumInstalledPackage b/c
                                       # it takes headers <shrug>
        if yip.pkgtup != po.pkgtup:
            if raiseError:
                raise URLGrabError(-1, 'Header does not match intended download')
            else:
                return 0
        
        return 1
        
    def downloadHeader(self, po):
        """download a header from a package object.
           output based on callback, raise yum.Errors.YumBaseError on problems"""

        if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
            return
                
        errors = {}
        local =  po.localHdr()
        start = po.returnSimple('hdrstart')
        end = po.returnSimple('hdrend')
        repo = self.repos.getRepo(po.repoid)
        remote = po.returnSimple('relativepath')
        if os.path.exists(local):
            try:
                result = self.verifyHeader(local, po, raiseError=1)
            except URLGrabError, e:
                # might add a check for length of file - if it is < 
                # required doing a reget
                try:
                    os.unlink(local)
                except OSError, e:
                    pass
            else:
                po.hdrpath = local
                return
        else:
            if self.conf.cache:
                raise Errors.RepoError, \
                'Header not in local cache and caching-only mode enabled. Cannot download %s' % remote
        
        if self.dsCallback: self.dsCallback.downloadHeader(po.name)
        
        try:
            checkfunc = (self.verifyHeader, (po, 1), {})
            hdrpath = repo.get(relative=remote, local=local, start=start,
                    reget=None, end=end, checkfunc=checkfunc, copy_local=1,
                    cache=repo.http_caching != 'none',
                    )
        except Errors.RepoError, e:
            saved_repo_error = e
            try:
                os.unlink(local)
            except OSError, e:
                raise Errors.RepoError, saved_repo_error
            else:
                raise
        else:
            po.hdrpath = hdrpath
            return

    def sigCheckPkg(self, po):
        '''Take a package object and attempt to verify GPG signature if required

        Returns (result, error_string) where result is
            0 - GPG signature verifies ok or verification is not required
            1 - GPG verification failed but installation of the right GPG key might help
            2 - Fatal GPG verifcation error, give up
        '''
        if hasattr(po, 'pkgtype') and po.pkgtype == 'local':
            check = self.conf.gpgcheck
            hasgpgkey = 0
        else:
            repo = self.repos.getRepo(po.repoid)
            check = repo.gpgcheck
            hasgpgkey = not not repo.gpgkey 
        
        if check:
            sigresult = rpmUtils.miscutils.checkSig(self.read_ts, po.localPkg())
            localfn = os.path.basename(po.localPkg())
            
            if sigresult == 0:
                result = 0
                msg = ''

            elif sigresult == 1:
                if hasgpgkey:
                    result = 1
                else:
                    result = 2
                msg = 'Public key for %s is not installed' % localfn

            elif sigresult == 2:
                result = 2
                msg = 'Problem opening package %s' % localfn

            elif sigresult == 3:
                if hasgpgkey:
                    result = 1
                else:
                    result = 2
                result = 1
                msg = 'Public key for %s is not trusted' % localfn

            elif sigresult == 4:
                result = 2 
                msg = 'Package %s is not signed' % localfn
            
        else:
            result =0
            msg = ''

        return result, msg
        
    def cleanHeaders(self):
        filelist = []
        ext = 'hdr'
        removed = 0
        for repo in self.repos.listEnabled():
            repo.dirSetup()
            path = repo.hdrdir
            filelist = misc.getFileList(path, ext, filelist)
            
        for hdr in filelist:
            try:
                os.unlink(hdr)
            except OSError, e:
                self.errorlog(0, 'Cannot remove header %s' % hdr)
                continue
            else:
                self.log(7, 'Header %s removed' % hdr)
                removed+=1
        msg = '%d headers removed' % removed
        return 0, [msg]

            
    def cleanPackages(self):
        filelist = []
        ext = 'rpm'
        removed = 0
        for repo in self.repos.listEnabled():
            repo.dirSetup()
            path = repo.pkgdir
            filelist = misc.getFileList(path, ext, filelist)
            
        for pkg in filelist:
            try:
                os.unlink(pkg)
            except OSError, e:
                self.errorlog(0, 'Cannot remove package %s' % pkg)
                continue
            else:
                self.log(7, 'Package %s removed' % pkg)
                removed+=1
        
        msg = '%d packages removed' % removed
        return 0, [msg]

    def cleanPickles(self):
        filelist = []
        ext = 'pickle'
        removed = 0
        for repo in self.repos.listEnabled():
            repo.dirSetup()
            path = repo.cachedir
            filelist = misc.getFileList(path, ext, filelist)
            
        for item in filelist:
            try:
                os.unlink(item)
            except OSError, e:
                self.errorlog(0, 'Cannot remove cache file %s' % item)
                continue
            else:
                self.log(7, 'Cache file %s removed' % item)
                removed+=1
        msg = '%d cache files removed' % removed
        return 0, [msg]
    
    def cleanSqlite(self):
        filelist = []
        ext = 'sqlite'
        removed = 0
        for repo in self.repos.listEnabled():
            repo.dirSetup()
            path = repo.cachedir
            filelist = misc.getFileList(path, ext, filelist)
            
        for item in filelist:
            try:
                os.unlink(item)
            except OSError, e:
                self.errorlog(0, 'Cannot remove sqlite cache file %s' % item)
                continue
            else:
                self.log(7, 'Cache file %s removed' % item)
                removed+=1
        msg = '%d cache files removed' % removed
        return 0, [msg]

    def cleanMetadata(self):
        filelist = []
        exts = ['xml.gz', 'xml', 'cachecookie']
        
        removed = 0
        for ext in exts:
            for repo in self.repos.listEnabled():
                repo.dirSetup()
                path = repo.cachedir
                filelist = misc.getFileList(path, ext, filelist)
            
        for item in filelist:
            try:
                os.unlink(item)
            except OSError, e:
                self.errorlog(0, 'Cannot remove metadata file %s' % item)
                continue
            else:
                self.log(7, 'metadata file %s removed' % item)
                removed+=1
        msg = '%d metadata files removed' % removed
        return 0, [msg]

    def doPackageLists(self, pkgnarrow='all'):
        """generates lists of packages, un-reduced, based on pkgnarrow option"""
        
        ygh = misc.GenericHolder()
        
        installed = []
        available = []
        updates = []
        obsoletes = []
        obsoletesTuples = []
        recent = []
        extras = []

        # list all packages - those installed and available, don't 'think about it'
        if pkgnarrow == 'all': 
            self.doRepoSetup()
            self.doRpmDBSetup()
            inst = self.rpmdb.getPkgList()
            for hdr in self.rpmdb.getHdrList():
                po = YumInstalledPackage(hdr)
                installed.append(po)

            if self.conf.showdupesfromrepos:
                avail = self.pkgSack.returnPackages()
            else:
                avail = self.pkgSack.returnNewestByNameArch()

            for pkg in avail:
                pkgtup = (pkg.name, pkg.arch, pkg.epoch, pkg.version, pkg.release)
                if pkgtup not in inst:
                    available.append(pkg)

        # produce the updates list of tuples
        elif pkgnarrow == 'updates':
            self.doRepoSetup()
            self.doRpmDBSetup()
            self.doUpdateSetup()
            for (n,a,e,v,r) in self.up.getUpdatesList():
                matches = self.pkgSack.searchNevra(name=n, arch=a, epoch=e, 
                                                   ver=v, rel=r)
                if len(matches) > 1:
                    updates.append(matches[0])
                    self.log(4, 'More than one identical match in sack for %s' % matches[0])
                elif len(matches) == 1:
                    updates.append(matches[0])
                else:
                    self.log(4, 'Nothing matches %s.%s %s:%s-%s from update' % (n,a,e,v,r))

        # installed only
        elif pkgnarrow == 'installed':
            self.doRpmDBSetup()
            for hdr in self.rpmdb.getHdrList():
                po = YumInstalledPackage(hdr)
                installed.append(po)
        
        # available in a repository
        elif pkgnarrow == 'available':
            self.doRepoSetup()
            self.doRpmDBSetup()
            inst = self.rpmdb.getPkgList()
            if self.conf.showdupesfromrepos:
                avail = self.pkgSack.returnPackages()
            else:
                avail = self.pkgSack.returnNewestByNameArch()

            for pkg in avail:
                pkgtup = (pkg.name, pkg.arch, pkg.epoch, pkg.version, pkg.release)
                if pkgtup not in inst:
                    available.append(pkg)

        # not in a repo but installed
        elif pkgnarrow == 'extras':
            # we must compare the installed set versus the repo set
            # anything installed but not in a repo is an extra
            self.doRepoSetup()
            self.doRpmDBSetup()
            avail = self.pkgSack.simplePkgList()
            for hdr in self.rpmdb.getHdrList():
                po = YumInstalledPackage(hdr)
                if po.pkgtup not in avail:
                    extras.append(po)

        # obsoleting packages (and what they obsolete)
        elif pkgnarrow == 'obsoletes':
            self.doRepoSetup()
            self.doRpmDBSetup()
            self.conf.obsoletes = 1
            self.doUpdateSetup()

            for (pkgtup, instTup) in self.up.getObsoletesTuples():
                (n,a,e,v,r) = pkgtup
                pkgs = self.pkgSack.searchNevra(name=n, arch=a, ver=v, rel=r, epoch=e)
                hdr = self.rpmdb.returnHeaderByTuple(instTup)[0] # the first one
                instpo = YumInstalledPackage(hdr)
                for po in pkgs:
                    obsoletes.append(po)
                    obsoletesTuples.append((po, instpo))
        
        # packages recently added to the repositories
        elif pkgnarrow == 'recent':
            now = time.time()
            recentlimit = now-(self.conf.recent*86400)
            ftimehash = {}
            self.doRepoSetup()
            if self.conf.showdupesfromrepos:
                avail = self.pkgSack.returnPackages()
            else:
                avail = self.pkgSack.returnNewestByNameArch()
            
            for po in avail:
                ftime = int(po.returnSimple('filetime'))
                if ftime > recentlimit:
                    if not ftimehash.has_key(ftime):
                        ftimehash[ftime] = [po]
                    else:
                        ftimehash[ftime].append(po)

            for sometime in ftimehash.keys():
                for po in ftimehash[sometime]:
                    recent.append(po)
        
        
        ygh.installed = installed
        ygh.available = available
        ygh.updates = updates
        ygh.obsoletes = obsoletes
        ygh.obsoletesTuples = obsoletesTuples
        ygh.recent = recent
        ygh.extras = extras

        
        return ygh

    def _refineSearchPattern(self, arg):
        """Takes a search string from the cli for Search or Provides
           and cleans it up so it doesn't make us vomit"""
        
        if re.match('.*[\*,\[,\],\{,\},\?,\+].*', arg):
            restring = fnmatch.translate(arg)
        else:
            restring = re.escape(arg)
            
        return restring
        
    def findDeps(self, pkgs):
        """Return the dependencies for a given package, as well
           possible solutions for those dependencies.
           
           Returns the deps as a dict  of:
             packageobject = [reqs] = [list of satisfying pkgs]"""
        
        results = {}
        self.doRepoSetup()
        self.doRpmDBSetup()

        avail = self.pkgSack.returnPackages()
        exactmatch, matched, unmatched = parsePackages(avail, pkgs)

        if len(unmatched) > 0:
            self.errorlog(0, 'No Match for arguments: %s' % unmatched)

        pkgs = misc.unique(exactmatch + matched)
        
        for pkg in pkgs:
            results[pkg] = {} 
            reqs = pkg.returnPrco('requires');
            reqs.sort()
            pkgresults = results[pkg] # shorthand so we don't have to do the
                                      # double bracket thing
            
            for req in reqs:
                (r,f,v) = req
                if r.startswith('rpmlib('):
                    continue
                
                satisfiers = []

                for po in self.whatProvides(r, f, v):
                    satisfiers.append(po)

                pkgresults[req] = satisfiers
        
        return results
    
    def searchGenerator(self, fields, criteria):
        """Generator method to lighten memory load for some searches.
           This is the preferred search function to use."""
        self.doRepoSetup()


        for string in criteria:
            restring = self._refineSearchPattern(string)
            try: crit_re = re.compile(restring, flags=re.I)
            except sre_constants.error, e:
                raise Errors.MiscError, \
                 'Search Expression: %s is an invalid Regular Expression.\n' % string
                  
            for po in self.pkgSack:
                tmpvalues = []
                for field in fields:
                    value = po.returnSimple(field)
                    if value and crit_re.search(value):
                        tmpvalues.append(value)
                
                if len(tmpvalues) > 0:
                    yield (po, tmpvalues)
        
        # do the same for installed pkgs
        
        self.doRpmDBSetup()        
        for hdr in self.rpmdb.getHdrList(): # this is more expensive so this is the  top op
            po = YumInstalledPackage(hdr)
            tmpvalues = []
            for string in criteria:
                restring = self._refineSearchPattern(string)
                
                try: crit_re = re.compile(restring, flags=re.I)
                except sre_constants.error, e:
                    raise Errors.MiscError, \
                     'Search Expression: %s is an invalid Regular Expression.\n' % string

                for field in fields:
                    value = po.returnSimple(field)
                    if type(value) is types.ListType: # this is annoying
                        value = str(value)
                    if value and crit_re.search(value):
                        tmpvalues.append(value)
                        
            if len(tmpvalues) > 0:
                yield (po, tmpvalues)        
        
    def searchPackages(self, fields, criteria, callback=None):
        """Search specified fields for matches to criteria
           optional callback specified to print out results
           as you go. Callback is a simple function of:
           callback(po, matched values list). It will 
           just return a dict of dict[po]=matched values list"""
        
        matches = {}
        match_gen = self.searchGenerator(fields, criteria)
        
        for (po, matched_strings) in match_gen:
            if callback:
                callback(po, matched_strings)
            if not matches.has_key(po):
                matches[po] = []
            
            matches[po].extend(matched_strings)
        
        return matches
    
    def searchPackageProvides(self, args, callback=None):
        
        self.doRepoSetup()
        matches = {}
        
        # search deps the simple way first
        for arg in args:
            self.log(4, 'searching the simple way')
            pkgs = self.returnPackagesByDep(arg)
            for po in pkgs:
                # if the match is already in the list, just skip it
                if matches.has_key(po):
                    continue
                
                matches[po] = [arg]
                if callback:
                    callback(po, [arg])
                

        # search pkgSack - fully populate the worthwhile metadata to search
        # if it even vaguely matches
        self.log(4, 'fully populating the necessary data')
        for arg in args:
            matched = 0
            globs = ['.*bin\/.*', '.*\/etc\/.*', '^\/usr\/lib\/sendmail$']
            for glob in globs:
                globc = re.compile(glob)
                if globc.match(arg):
                    matched = 1
            if not matched:
                self.doSackFilelistPopulate()

        for arg in args:
            # assume we have to search every package, unless we can refine the search set
            where = self.pkgSack
            
            # this is annoying. If the user doesn't use any glob or regex-like
            # or regexes then we can use the where 'like' search in sqlite
            # if they do use globs or regexes then we can't b/c the string
            # will no longer have much meaning to use it for matches
            
            if hasattr(self.pkgSack, 'searchAll'):
                if not re.match('.*[\*,\[,\],\{,\},\?,\+,\%].*', arg):
                    self.log(4, 'Using the like search on %s' % arg)
                    where = self.pkgSack.searchAll(arg, query_type='like')
            
            self.log(4, 'Searching %d packages' % len(where))
            self.log(4, 'refining the search expression of %s' % arg) 
            restring = self._refineSearchPattern(arg)
            self.log(4, 'refined search: %s' % restring)
            try: 
                arg_re = re.compile(restring, flags=re.I)
            except sre_constants.error, e:
                raise Errors.MiscError, \
                  'Search Expression: %s is an invalid Regular Expression.\n' % arg

            for po in where:
                self.log(5, 'searching package %s' % po)
                tmpvalues = []
                
                self.log(5, 'searching in file entries')
                for filetype in po.returnFileTypes():
                    for fn in po.returnFileEntries(ftype=filetype):
                        if arg_re.search(fn):
                            tmpvalues.append(fn)
                
                self.log(5, 'searching in provides entries')
                for (p_name, p_flag, (p_e, p_v, p_r)) in po.returnPrco('provides'):
                    if arg_re.search(p_name):
                        prov = po.prcoPrintable((p_name, p_flag, (p_e, p_v, p_r)))
                        tmpvalues.append(prov)

                if len(tmpvalues) > 0:
                    if not matches.has_key(po):
                        matches[po] = tmpvalues
                        if callback:
                            callback(po, tmpvalues)
                
        
        self.doRpmDBSetup()
        # installed rpms, too
        taglist = ['filenames', 'dirnames', 'provides']
        arg_re = []
        for arg in args:
            restring = self._refineSearchPattern(arg)

            try: reg = re.compile(restring, flags=re.I)
            except sre_constants.error, e:
                raise Errors.MiscError, \
                 'Search Expression: %s is an invalid Regular Expression.\n' % arg
            
            arg_re.append(reg)

        for hdr in self.rpmdb.getHdrList():
            po = YumInstalledPackage(hdr)
            tmpvalues = []
            searchlist = []
            for tag in taglist:
                tagdata = po.returnSimple(tag)
                if tagdata is None:
                    continue
                if type(tagdata) is types.ListType:
                    searchlist.extend(tagdata)
                else:
                    searchlist.append(tagdata)
            
            for reg in arg_re:
                for item in searchlist:
                    if reg.search(item):
                        tmpvalues.append(item)

            del searchlist

            if len(tmpvalues) > 0:
                if callback:
                    callback(po, tmpvalues)
                matches[po] = tmpvalues
            
            
        return matches

    def doGroupLists(self, uservisible=0):
        """returns two lists of groups, installed groups and available groups
           optional 'uservisible' bool to tell it whether or not to return
           only groups marked as uservisible"""
        
        
        installed = []
        available = []
        
        for grp in self.comps.groups:
            if grp.installed:
                if uservisible:
                    if grp.user_visible:
                        installed.append(grp)
                else:
                    installed.append(grp)
            else:
                if uservisible:
                    if grp.user_visible:
                        available.append(grp)
                else:
                    available.append(grp)
            
        return installed, available
    
    
    def groupRemove(self, grpid):
        """mark all the packages in this group to be removed"""
        
        txmbrs_used = []
        self.doGroupSetup()
        
        thisgroup = self.comps.return_group(grpid)
        if not thisgroup:
            raise Errors.GroupsError, "No Group named %s exists" % grpid

        thisgroup.toremove = True
        pkgs = thisgroup.packages
        for pkg in thisgroup.packages:
            txmbrs = self.remove(name=pkg)
            txmbrs_used.extend(txmbrs)
            for txmbr in txmbrs:
                txmbr.groups.append(thisgroup.groupid)
        
        return txmbrs_used

    def groupUnremove(self, grpid):
        """unmark any packages in the group from being removed"""
        
        self.doGroupSetup()

        thisgroup = self.comps.return_group(grpid)
        if not thisgroup:
            raise Errors.GroupsError, "No Group named %s exists" % grpid

        thisgroup.toremove = False
        pkgs = thisgroup.packages
        for pkg in thisgroup.packages:
            for txmbr in self.tsInfo:
                if txmbr.po.name == pkg and txmbr.po.state in TS_INSTALL_STATES:
                    try:
                        txmbr.groups.remove(grpid)
                    except ValueError:
                        self.log(4, "package %s was not marked in group %s" % (txmbr.po, grpid))
                        continue
                    
                    # if there aren't any other groups mentioned then remove the pkg
                    if len(txmbr.groups) == 0:
                        self.tsInfo.remove(txmbr.po.pkgtup)
        
        
    def selectGroup(self, grpid):
        """mark all the packages in the group to be installed
           returns a list of transaction members it added to the transaction 
           set"""
        
        txmbrs_used = []
        if not self.comps:
            self.doGroupSetup()
        
        if not self.comps.has_group(grpid):
            raise Errors.GroupsError, "No Group named %s exists" % grpid
            
        thisgroup = self.comps.return_group(grpid)
        
        if not thisgroup:
            raise Errors.GroupsError, "No Group named %s exists" % grpid
        
        if thisgroup.selected:
            return txmbrs_used
        
        thisgroup.selected = True
        
        pkgs = thisgroup.mandatory_packages.keys() + thisgroup.default_packages.keys()
        for pkg in pkgs:
            self.log(5, 'Adding package %s from group %s' % (pkg, thisgroup.groupid))
            try:
                txmbrs = self.install(name = pkg)
            except Errors.InstallError, e:
                self.log(3, 'No package named %s available to be installed' % pkg)
            else:
                txmbrs_used.extend(txmbrs)
                for txmbr in txmbrs:
                    txmbr.groups.append(thisgroup.groupid)
        
        if self.conf.enable_group_conditionals:
            for condreq, cond in thisgroup.conditional_packages.iteritems():
                if self._isPackageInstalled(cond):
                    try:
                        txmbrs = self.install(name = condreq)
                    except Errors.InstallError:
                        # we don't care if the package doesn't exist
                        continue
                    txmbrs_used.extend(txmbrs)
                    for txmbr in txmbrs:
                        txmbr.groups.append(thisgroup.groupid)
                    continue
                # Otherwise we hook into tsInfo.add
                pkgs = self.pkgSack.searchNevra(name=condreq)
                if pkgs:
                    pkgs = self.bestPackagesFromList(pkgs)
                if self.tsInfo.conditionals.has_key(cond):
                    self.tsInfo.conditionals[cond].extend(pkgs)
                else:
                    self.tsInfo.conditionals[cond] = pkgs

        return txmbrs_used

    def deselectGroup(self, grpid):
        """de-mark all the packages in the group for install"""
        if not self.comps:
            self.doGroupSetup()
        
        if not self.comps.has_group(grpid):
            raise Errors.GroupsError, "No Group named %s exists" % grpid
            
        thisgroup = self.comps.return_group(grpid)
        if not thisgroup:
            raise Errors.GroupsError, "No Group named %s exists" % grpid
        
        thisgroup.selected = False
        
        for pkgname in thisgroup.packages:
        
            for txmbr in self.tsInfo:
                if txmbr.po.name == pkgname and txmbr.po.state in TS_INSTALL_STATES:
                    try: 
                        txmbr.groups.remove(grpid)
                    except ValueError:
                        self.log(4, "package %s was not marked in group %s" % (txmbr.po, grpid))
                        continue
                    
                    # if there aren't any other groups mentioned then remove the pkg
                    if len(txmbr.groups) == 0:
                        self.tsInfo.remove(txmbr.po.pkgtup)

                    
        
    def getPackageObject(self, pkgtup):
        """retrieves a packageObject from a pkgtuple - if we need
           to pick and choose which one is best we better call out
           to some method from here to pick the best pkgobj if there are
           more than one response - right now it's more rudimentary."""
           
        
        (n,a,e,v,r) = pkgtup
        
        # look it up in the self.localPackages first:
        for po in self.localPackages:
            if po.pkgtup == pkgtup:
                return po
                
        pkgs = self.pkgSack.packagesByTuple(pkgtup)

        if len(pkgs) == 0:
            raise Errors.DepError, 'Package tuple %s could not be found in packagesack' % str(pkgtup)
            return None
            
        if len(pkgs) > 1: # boy it'd be nice to do something smarter here FIXME
            result = pkgs[0]
        else:
            result = pkgs[0] # which should be the only
        
            # this is where we could do something to figure out which repository
            # is the best one to pull from
        
        return result

    def getInstalledPackageObject(self, pkgtup):
        """returns a YumInstallPackage object for the pkgtup specified"""
        
        hdrs = self.rpmdb.returnHeaderByTuple(pkgtup)
        hdr = hdrs[0]
        po = YumInstalledPackage(hdr)
        return po
        
    def gpgKeyCheck(self):
        """checks for the presence of gpg keys in the rpmdb
           returns 0 if no keys returns 1 if keys"""

        gpgkeyschecked = self.conf.cachedir + '/.gpgkeyschecked.yum'
        if os.path.exists(gpgkeyschecked):
            return 1
            
        myts = rpmUtils.transaction.initReadOnlyTransaction(root=self.conf.installroot)
        myts.pushVSFlags(~(rpm._RPMVSF_NOSIGNATURES|rpm._RPMVSF_NODIGESTS))
        idx = myts.dbMatch('name', 'gpg-pubkey')
        keys = idx.count()
        del idx
        del myts
        
        if keys == 0:
            return 0
        else:
            mydir = os.path.dirname(gpgkeyschecked)
            if not os.path.exists(mydir):
                os.makedirs(mydir)
                
            fo = open(gpgkeyschecked, 'w')
            fo.close()
            del fo
            return 1

    def returnPackagesByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the packages it finds providing that dep."""
        
        results = []
        self.doRepoSetup()
        # parse the string out
        #  either it is 'dep (some operator) e:v-r'
        #  or /file/dep
        #  or packagename
        depname = depstring
        depflags = None
        depver = None
        
        if depstring[0] != '/':
            # not a file dep - look at it for being versioned
            if re.search('[>=<]', depstring):  # versioned
                try:
                    depname, flagsymbol, depver = depstring.split()
                except ValueError, e:
                    raise Errors.YumBaseError, 'Invalid versioned dependency string, try quoting it.'
                if not SYMBOLFLAGS.has_key(flagsymbol):
                    raise Errors.YumBaseError, 'Invalid version flag'
                depflags = SYMBOLFLAGS[flagsymbol]
                
        sack = self.whatProvides(depname, depflags, depver)
        results = sack.returnPackages()
        return results
        

    def returnPackageByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the best(or first) package it finds providing that dep."""
        
        try:
            pkglist = self.returnPackagesByDep(depstring)
        except Errors.YumBaseError, e:
            raise Errors.YumBaseError, 'No Package found for %s' % depstring
        
        result = self._bestPackageFromList(pkglist)
        if result is None:
            raise Errors.YumBaseError, 'No Package found for %s' % depstring
        
        return result

    def returnInstalledPackagesByDep(self, depstring):
        """Pass in a generic [build]require string and this function will 
           pass back the installed packages it finds providing that dep."""
        
        results = []
        self.doRpmDBSetup()
        # parse the string out
        #  either it is 'dep (some operator) e:v-r'
        #  or /file/dep
        #  or packagename
        depname = depstring
        depflags = None
        depver = None
        
        if depstring[0] != '/':
            # not a file dep - look at it for being versioned
            if re.search('[>=<]', depstring):  # versioned
                try:
                    depname, flagsymbol, depver = depstring.split()
                except ValueError, e:
                    raise Errors.YumBaseError, 'Invalid versioned dependency string, try quoting it.'
                if not SYMBOLFLAGS.has_key(flagsymbol):
                    raise Errors.YumBaseError, 'Invalid version flag'
                depflags = SYMBOLFLAGS[flagsymbol]
                
        pkglist = self.rpmdb.whatProvides(depname, depflags, depver)
        
        for pkgtup in pkglist:
            results.append(self.getInstalledPackageObject(pkgtup))
        
        return results


    def _bestPackageFromList(self, pkglist):
        """take list of package objects and return the best package object.
           If the list is empty, return None. 
           
           Note: this is not aware of multilib so make sure you're only
           passing it packages of a single arch group."""
        
        
        if len(pkglist) == 0:
            return None
            
        if len(pkglist) == 1:
            return pkglist[0]
        
        mysack = ListPackageSack()
        mysack.addList(pkglist)
        bestlist = mysack.returnNewestByNameArch() # get rid of all lesser vers
        
        best = bestlist[0]
        for pkg in bestlist[1:]:
            if len(pkg.name) < len(best.name): # shortest name silliness
                best = pkg
                continue
            elif len(pkg.name) > len(best.name):
                continue

            # compare arch
            arch = rpmUtils.arch.getBestArchFromList([pkg.arch, best.arch])
            if arch == pkg.arch:
                best = pkg
                continue

        return best

    def bestPackagesFromList(self, pkglist, arch=None):
        """Takes a list of packages, returns the best packages.
           This function is multilib aware so that it will not compare
           multilib to singlelib packages""" 
    
        returnlist = []
        compatArchList = rpmUtils.arch.getArchList(arch)
        multiLib = []
        singleLib = []
        for po in pkglist:
            if po.arch not in compatArchList:
                continue
            elif rpmUtils.arch.isMultiLibArch(arch=po.arch):
                multiLib.append(po)
            else:
                singleLib.append(po)
                
        # we should have two lists now - one of singleLib packages
        # one of multilib packages
        # go through each one and find the best package(s)
        for pkglist in [multiLib, singleLib]:
            best = self._bestPackageFromList(pkglist)
            if best is not None:
                returnlist.append(best)
        
        return returnlist


    def install(self, po=None, **kwargs):
        """try to mark for install the item specified. Uses provided package 
           object, if available. If not it uses the kwargs and gets the best
           packages from the keyword options provided 
           returns the list of txmbr of the items it installs
           
           """
        
        self.doRepoSetup()
        self.doSackSetup()
        self.doRpmDBSetup()
        self.doUpdateSetup()
        
        pkgs = []
        if po:
            if isinstance(po, YumAvailablePackage) or isinstance(po, YumLocalPackage):
                pkgs.append(po)
            else:
                raise Errors.InstallError, 'Package Object was not a package object instance'
            
        else:
            if not kwargs.keys():
                raise Errors.InstallError, 'Nothing specified to install'

            if kwargs.has_key('pattern'):
                exactmatch, matched, unmatched = \
                    parsePackages(self.pkgSack.returnPackages(),[kwargs['pattern']] , casematch=1)
                pkgs.extend(exactmatch)
                pkgs.extend(matched)

            else:
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                pkgs = self.pkgSack.searchNevra(name=nevra_dict['name'],
                     epoch=nevra_dict['epoch'], arch=nevra_dict['arch'],
                     ver=nevra_dict['version'], rel=nevra_dict['release'])
                
                if pkgs:
                    pkgs = self.bestPackagesFromList(pkgs)

        if len(pkgs) == 0:
            #FIXME - this is where we could check to see if it already installed
            # for returning better errors
            raise Errors.InstallError, 'No package(s) available to install'
        
        # FIXME - lots more checking here
        #  - install instead of erase
        #  - better error handling/reporting
        
        tx_return = []
        for po in pkgs:
            if self.tsInfo.exists(pkgtup=po.pkgtup):
                self.log(4, 'Package: %s  - already in transaction set' % po)
                tx_return.extend(self.tsInfo.getMembers(pkgtup=po.pkgtup))
                continue
            
            # make sure this shouldn't be passed to update:
            if self.up.updating_dict.has_key(po.pkgtup):
                txmbrs = self.update(po=po)
                tx_return.extend(txmbrs)
                continue
            
            # make sure it's not already installed
            if self.rpmdb.installed(name=po.name, arch=po.arch, epoch=po.epoch,
                    rel=po.rel, ver=po.ver):
                self.errorlog(2, 'Package %s already installed and latest version' % po)
                continue

            txmbr = self.tsInfo.addInstall(po)
            tx_return.append(txmbr)
        
        return tx_return

    
    def update(self, po=None, **kwargs):
        """try to mark for update the item(s) specified. 
            po is a package object - if that is there, mark it for update,
            if possible
            else use **kwargs to match the package needing update
            if nothing is specified at all then attempt to update everything
            
            returns the list of txmbr of the items it marked for update"""
        
        # do updates list
        # do obsoletes list
        
        # check for args - if no po nor kwargs, do them all
        # if po, do it, ignore all else
        # if no po do kwargs
        # uninstalled pkgs called for update get returned with errors in a list, maybe?

        self.doRepoSetup()
        self.doSackSetup()
        self.doRpmDBSetup()
        self.doUpdateSetup()
        updates = self.up.getUpdatesTuples()
        if self.conf.obsoletes:
            obsoletes = self.up.getObsoletesTuples(newest=1)
        else:
            obsoletes = []


        tx_return = []
        if not po and not kwargs.keys(): # update everything (the easy case)
            self.log(5, 'Updating Everything')
            for (obsoleting, installed) in obsoletes:
                obsoleting_pkg = self.getPackageObject(obsoleting)
                hdr = self.rpmdb.returnHeaderByTuple(installed)[0]
                installed_pkg =  YumInstalledPackage(hdr)
                txmbr = self.tsInfo.addObsoleting(obsoleting_pkg, installed_pkg)
                self.tsInfo.addObsoleted(installed_pkg, obsoleting_pkg)
                tx_return.append(txmbr)
                
            for (new, old) in updates:
                if self.tsInfo.isObsoleted(pkgtup=old):
                    self.log(5, 'Not Updating Package that is already obsoleted: %s.%s %s:%s-%s' % old)
                else:
                    updating_pkg = self.getPackageObject(new)
                    hdr = self.rpmdb.returnHeaderByTuple(old)[0]
                    updated_pkg = YumInstalledPackage(hdr)
                    txmbr = self.tsInfo.addUpdate(updating_pkg, updated_pkg)
                    tx_return.append(txmbr)
            
            return tx_return

        else:
            instpkgs = []
            availpkgs = []
            if po: # just a po
                if po.repoid == 'installed':
                    instpkgs.append(po)
                else:
                    availpkgs.append(po)
                
            else: # we have kwargs, sort them out.
                nevra_dict = self._nevra_kwarg_parse(kwargs)

                availpkgs = self.pkgSack.searchNevra(name=nevra_dict['name'],
                          epoch=nevra_dict['epoch'], arch=nevra_dict['arch'],
                        ver=nevra_dict['version'], rel=nevra_dict['release'])
                
                installed_tuples = self.rpmdb.returnTupleByKeyword(
                                name=nevra_dict['name'], epoch=nevra_dict['epoch'],
                                arch=nevra_dict['arch'], ver=nevra_dict['version'],
                                rel=nevra_dict['release'])
            
                for tup in installed_tuples:
                    hdr = self.rpmdb.returnHeaderByTuple(tup)[0]
                    installed_pkg =  YumInstalledPackage(hdr)
                    instpkgs.append(installed_pkg)
            
            # for any thing specified
            # get the list of available pkgs matching it (or take the po)
            # get the list of installed pkgs matching it (or take the po)
            # go through each list and look for:
               # things obsoleting it if it is an installed pkg
               # things it updates if it is an available pkg
               # things updating it if it is an installed pkg
               # in that order
               # all along checking to make sure we:
                # don't update something that's already been obsoleted
            
            # TODO: we should search the updates and obsoletes list and
            # mark the package being updated or obsoleted away appropriately
            # and the package relationship in the tsInfo
            
            for installed_pkg in instpkgs:
                if self.up.obsoleted_dict.has_key(installed_pkg.pkgtup) and self.conf.obsoletes:
                    obsoleting = self.up.obsoleted_dict[installed_pkg.pkgtup][0]
                    obsoleting_pkg = self.getPackageObject(obsoleting)
                    # FIXME check for what might be in there here
                    txmbr = self.tsInfo.addObsoleting(obsoleting_pkg, installed_pkg)
                    self.tsInfo.addObsoleted(installed_pkg, obsoleting_pkg)
                    tx_return.append(txmbr)
            
            for available_pkg in availpkgs:
                if self.up.updating_dict.has_key(available_pkg.pkgtup):
                    updated = self.up.updating_dict[available_pkg.pkgtup][0]
                    if self.tsInfo.isObsoleted(updated):
                        self.log(5, 'Not Updating Package that is already obsoleted: %s.%s %s:%s-%s' % updated)
                    else:
                        hdr = self.rpmdb.returnHeaderByTuple(updated)[0]
                        updated_pkg =  YumInstalledPackage(hdr)
                        txmbr = self.tsInfo.addUpdate(available_pkg, updated_pkg)
                        tx_return.append(txmbr)
                    
            for installed_pkg in instpkgs:
                if self.up.updatesdict.has_key(installed_pkg.pkgtup):
                    updating = self.up.updatesdict[installed_pkg.pkgtup][0]
                    updating_pkg = self.getPackageObject(updating)
                    if self.tsInfo.isObsoleted(installed_pkg.pkgtup):
                        self.log(5, 'Not Updating Package that is already obsoleted: %s.%s %s:%s-%s' % installed_pkg.pkgtup)
                    else:
                        txmbr = self.tsInfo.addUpdate(updating_pkg, installed_pkg)
                        tx_return.append(txmbr)

        return tx_return
        
        
    def remove(self, po=None, **kwargs):
        """try to find and mark for remove the specified package(s) -
            if po is specified then that package object (if it is installed) 
            will be marked for removal.
            if no po then look at kwargs, if neither then raise an exception"""

        if not po and not kwargs.keys():
            raise Errors.RemoveError, 'Nothing specified to remove'
        
        self.doRpmDBSetup()
        tx_return = []
        pkgs = []
        
        if po:
            pkgs = [po]
        else:
            nevra_dict = self._nevra_kwarg_parse(kwargs)

            installed_tuples = self.rpmdb.returnTupleByKeyword(
                            name=nevra_dict['name'], epoch=nevra_dict['epoch'],
                            arch=nevra_dict['arch'], ver=nevra_dict['version'],
                            rel=nevra_dict['release'])

            for tup in installed_tuples:
                hdr = self.rpmdb.returnHeaderByTuple(tup)[0]
                installed_pkg =  YumInstalledPackage(hdr)
                pkgs.append(installed_pkg)

        
        if len(pkgs) == 0: # should this even be happening?
            self.errorlog(3, "No package matched to remove")

        for po in pkgs:
            txmbr = self.tsInfo.addErase(po)
            tx_return.append(txmbr)
        
        return tx_return

    def _nevra_kwarg_parse(self, kwargs):
            
        returndict = {}
        
        try: returndict['name'] = kwargs['name']
        except KeyError:  returndict['name'] = None

        try: returndict['epoch'] = kwargs['epoch']
        except KeyError: returndict['epoch'] = None

        try: returndict['arch'] = kwargs['arch']
        except KeyError: returndict['arch'] = None
        
        # get them as ver, version and rel, release - if someone
        # specifies one of each then that's kinda silly.
        try: returndict['version'] = kwargs['version']
        except KeyError: returndict['version'] = None
        if returndict['version'] is None:
            try: returndict['version'] = kwargs['ver']
            except KeyError: returndict['version'] = None

        try: returndict['release'] = kwargs['release']
        except KeyError: returndict['release'] = None
        if returndict['release'] is None:
            try: release = kwargs['rel']
            except KeyError: returndict['release'] = None
        
        return returndict

    def _isPackageInstalled(self, pkgname):
        # FIXME: Taken from anaconda/pirut 
        # clean up and make public
        installed = False
        if self.rpmdb.installed(name = pkgname):
            installed = True

        lst = self.tsInfo.matchNaevr(name = pkgname)
        for txmbr in lst:
            if txmbr.output_state in TS_INSTALL_STATES:
                return True
        if installed and len(lst) > 0:
            # if we get here, then it was installed, but it's in the tsInfo
            # for an erase or obsoleted --> not going to be installed at end
            return False
        return installed
