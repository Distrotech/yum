#!/usr/bin/python
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

import os
import clientStuff
import rpm
import string
import sys
import archwork
import rpmUtils
from i18n import _


class nevral:
    def __init__(self):
        self.rpmbyname = {}
        self.rpmbynamearch = {}
        self.localrpmpath = {}
        self.localhdrpath = {}
        
    def add(self,(name,epoch,ver,rel,arch,rpmloc,serverid),state):
#        if self.rpmbyname.haskey(name):
#            ((e,v,r,a,l,i),state) = self._get_data(name)
#            goodarch = clientStuff.betterarch(arch,a)
#            if goodarch != None:
#                if goodarch == arch:
#                    self.rpmbyname[name]=((epoch,ver,rel,arch,rpmloc,serverid), state)
        self.rpmbyname[name]=((epoch,ver,rel,arch,rpmloc,serverid), state)
        self.rpmbynamearch[(name,arch)]=((epoch,ver,rel,arch,rpmloc,serverid), state)
        
    def _get_data(self, name, arch=None):
        if arch != None: # search by name and arch
            if self.rpmbynamearch and self.rpmbynamearch.has_key((name, arch)):
                return self.rpmbynamearch[(name, arch)]
            else: 
                return ((None,None,None,None,None,None),None)
        else:            # search by name only
            if self.rpmbyname and self.rpmbyname.has_key(name):
                return self.rpmbyname[name]
            else: 
                return ((None,None,None,None,None,None),None)

    def getHeader(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None:
            errorlog(0, _('Header for pkg %s not found') % (name))
            #FIXME: should return none and/or raise Exception
            sys.exit(1)
            return None
        else: 
            if l == 'in_rpm_db':
                # we're in the rpmdb - get the header from there
                hindexes = ts.dbMatch('name', name)
                for hdr in hindexes:
                    return hdr
            else:
                # we're in a .hdr file
                pkghdr = clientStuff.readHeader(self.localHdrPath(name, arch))
                if pkghdr == None:
                    errorlog(0, _('Bad Header for pkg %s.%s trying to get headers for the nevral - exiting') % (name, arch))
                    # FIXME - should raise exception and be handled elsewhere
                    sys.exit(1)
                else:
                    return pkghdr
                    

    def NAkeys(self):
        keys = self.rpmbynamearch.keys()
        return keys

    def Nkeys(self):
        keys = self.rpmbyname.keys()
        return keys

    def hdrfn(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None: 
            return None
        else: 
            return '%s-%s-%s-%s.%s.hdr' % (name, e, v, r, a)

    def rpmlocation(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None: 
            return None
        else:
            return l
      
    def evr(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None: 
            return (None, None, None)
        else:
            return (e, v, r)

    def exists(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None: 
            return 0
        else:
            return 1

    def state(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None: 
            return None
        else:
            return state
            
    def serverid(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None: 
            return None
        else:
            return i
    
    def nafromloc(self, loc):
        keys = self.rpmbynamearch.keys()
        for (name, arch) in keys:
            ((e,v,r,a,l,i),state) = self._get_data(name, arch)
            if state == None: 
                return None
            else:
                if l == loc:
                    return (name,arch)
    
    def remoteHdrUrl(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None:
            return None
        if l == 'in_rpm_db':
            return l
        hdrfn = self.hdrfn(name,arch)
        base = conf.serverurl[i]
        return base + '/headers/' + hdrfn
    
    def localHdrPath(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None:
            return None
        if l == 'in_rpm_db':
            return l
        if self.localhdrpath.has_key((name, arch)):
            return self.localhdrpath[(name, arch)]
        else:
            hdrfn = self.hdrfn(name,arch)
            base = conf.serverhdrdir[i]
            log(7, 'localhdrpath= %s for %s %s' % (base + '/' + hdrfn, name, arch))
            return base + '/' + hdrfn
        
    def setlocalhdrpath(self, name, arch, path):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        self.localhdrpath[(name, arch)] = path

    def remoteRpmUrl(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None:
            return None
        if l == 'in_rpm_db':
            return l
        base = conf.serverurl[i]
        return base +'/'+ l
    
    def localRpmPath(self, name, arch=None):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        if state == None:
            return None
        if l == 'in_rpm_db':
            return l
        if self.localrpmpath.has_key((name, arch)):
            return self.localrpmpath[(name, arch)]
        else:
            rpmfn = os.path.basename(l)
            base = conf.serverpkgdir[i]
            return base + '/' + rpmfn
    
    def setlocalrpmpath(self, name, arch, path):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        self.localrpmpath[(name, arch)] = path

    def setPkgState(self, name, arch, newstate):
        ((e,v,r,a,l,i),state) = self._get_data(name, arch)
        self.add((name,e,v,r,arch,l,i), newstate)
        
    def populateTs(self, addavailable = 1):
        installonlypkgs = ['kernel', 'kernel-bigmem', 'kernel-enterprise',
                           'kernel-smp', 'kernel-debug']
                           
        _ts = rpmUtils.Rpm_Ts_Work('/')
        for (name, arch) in self.NAkeys(): 
            if self.state(name, arch) in ('u','ud','iu'):
                log(4,'Updating: %s, %s' % (name, arch))
                rpmloc = self.rpmlocation(name, arch)
                pkghdr = self.getHeader(name, arch)
                if name in installonlypkgs:
                    kernarchlist = archwork.availablearchs(self,name)
                    bestarch = archwork.bestarch(kernarchlist)
                    if arch == bestarch:
                        log(3, 'Found best kernel arch: %s' %(arch))
                        _ts.addInstall(pkghdr,(pkghdr,rpmloc),'i')
                        self.setPkgState(name, arch, 'i')
                    else:
                        log(3, 'Removing dumb kernel with silly arch %s' %(arch))
                        if addavailable:
                            _ts.addInstall(pkghdr,(pkghdr,rpmloc),'a')
                        self.setPkgState(name, arch, 'a')
                else:
                    log(5, 'Not a kernel, adding to ts')
                    _ts.addInstall(pkghdr,(pkghdr,rpmloc),'u')
                    
            elif self.state(name,arch) == 'i':
                log(4, 'Installing: %s, %s' % (name, arch))
                rpmloc = self.rpmlocation(name, arch)
                pkghdr = self.getHeader(name, arch)
                _ts.addInstall(pkghdr,(pkghdr,rpmloc),'i')
            elif self.state(name,arch) == 'a':
                if addavailable:
                    log(7, 'Adding %s into \'a\' state' % name)
                    rpmloc = self.rpmlocation(name, arch)
                    pkghdr = self.getHeader(name, arch)
                    _ts.addInstall(pkghdr,(pkghdr,rpmloc),'a')
                else:
                    pass
            elif self.state(name,arch) == 'e' or self.state(name,arch) == 'ed':
                log(4, 'Erasing: %s-%s' % (name,arch))
                _ts.addErase(name)
        return _ts
        
    def resolvedeps(self, rpmDBInfo):
        #self == tsnevral
        #populate ts
        #depcheck
        #parse deps, if they exist, change nevral pkg states
        #die if:
        #    no suggestions
        #    conflicts
        #return 0 and a message if all is fine
        #return 1 and a list of error messages if shit breaks
        CheckDeps = 1
        conflicts = 0
        unresolvable = 0
        
        # this does a quick dep check with adding all the archs
        # keeps mem usage small in the easy/quick case
        _ts = self.populateTs(addavailable = 0)
        deps = _ts.check()
        if not deps:
            log(5, 'Quick Check only')
            return (0, 'Success - deps resolved')
        del deps
        del _ts
        log(5, 'Long Check')
        while CheckDeps==1 or (conflicts != 1 and unresolvable != 1 ):
            errors=[]
            _ts = self.populateTs(addavailable = 1)
            deps = _ts.check()
            
            CheckDeps = 0
            if not deps:
                return (0, 'Success - deps resolved')
            log (3, '# of Deps = %d' % len(deps))
            for ((name, version, release), (reqname, reqversion),
                                flags, suggest, sense) in deps:
                log (4, 'dep: %s req %s - %s - %s' % (name, reqname, reqversion, sense))
                if sense == rpm.RPMDEP_SENSE_REQUIRES:
                    if suggest:
                        (header, sugname) = suggest
                        log(4, '%s wants %s' % (name, sugname))
                        (name, arch) = self.nafromloc(sugname)
                        archlist = archwork.availablearchs(self,name)
                        bestarch = archwork.bestarch(archlist)
                        log(3, 'bestarch = %s for %s' % (bestarch, name))
                        self.setPkgState(name, bestarch, 'ud')
                        log(4, 'Got dep: %s, %s' % (name,bestarch))
                        CheckDeps = 1
                    else:
                        if self.exists(reqname):
                            if self.state(reqname) in ('e', 'ed'):
                                # this is probably an erase depedency
                                archlist = archwork.availablearchs(rpmDBInfo,name)
                                arch = archwork.bestarch(archlist)
                                ((e, v, r, a, l, i), s)=rpmDBInfo._get_data(name,arch)
                                self.add((name,e,v,r,arch,l,i),'ed')
                                log(4, 'Got Erase Dep: %s, %s' %(name,arch))
                            else:
                                archlist = archwork.availablearchs(self,name)
                                if len(archlist) > 0:
                                    arch = archwork.bestarch(archlist)
                                    self.setPkgState(name, arch, 'ud')
                                    log(4, 'Got Extra Dep: %s, %s' %(name,arch))
                                else:
                                    unresolvable = 1
                                    log(4, 'unresolvable - %s needs %s' % (name, rpmUtils.formatRequire(reqname, reqversion, flags)))
                                    if clientStuff.nameInExcludes(reqname):
                                        errors.append('package %s needs %s that has been excluded' % (name, reqname))
                                    else:
                                        errors.append('package %s needs %s (not provided)' % (name, rpmUtils.formatRequire(reqname, reqversion, flags)))
                            CheckDeps=1
                        else:
                            # this is horribly ugly but I have to find some way to see if what it needed is provided
                            # by what we are removing - if it is then remove it -otherwise its a real dep problem - move along
                            if reqname[0] == '/':
                                whatprovides = _ts.dbMatch('basenames', reqname)
                            else:
                                whatprovides = _ts.dbMatch('provides', reqname)

                            if whatprovides and whatprovides.count() != 0:
                                for provhdr in whatprovides:
                                    if self.state(provhdr[rpm.RPMTAG_NAME],provhdr[rpm.RPMTAG_ARCH]) in ('e','ed'):
                                        ((e,v,r,arch,l,i),s)=rpmDBInfo._get_data(name)
                                        self.add((name,e,v,r,arch,l,i),'ed')
                                        log(4, 'Got Erase Dep: %s, %s' %(name,arch))
                                        CheckDeps=1
                                    else:
                                        unresolvable = 1
                                        if clientStuff.nameInExcludes(reqname):
                                            errors.append('package %s needs %s that has been excluded' % (name, reqname))
                                            log(5, 'Got to an unresolvable dep - %s %s' %(name,arch))
                                        else:
                                            errors.append('package %s needs %s (not provided)' % (name, rpmUtils.formatRequire(reqname, reqversion, flags)))
                            else:
                                unresolvable = 1
                                if clientStuff.nameInExcludes(reqname):
                                    errors.append('package %s needs %s that has been excluded' % (name, reqname))
                                else:
                                    errors.append('package %s needs %s (not provided)' % (name, rpmUtils.formatRequire(reqname, reqversion, flags)))
                elif sense == rpm.RPMDEP_SENSE_CONFLICTS:
                    # much more shit should happen here. specifically:
                    # if you have a conflict b/t two pkgs, try to upgrade the reqname pkg. - see if that solves the problem
                    # also something like a "this isn't our fault and we can't help it, continue on" should happen.like in anaconda
                    # more even than the below should happen here - but its getting closer - I need to flesh out all the horrible
                    # states it could be in.
                    log(4, 'conflict: %s %s %s' % (name, reqname, reqversion))
                    if rpmDBInfo.exists(reqname) and self.exists(reqname) and self.state(reqname) not in ('i','iu','u','ud'):
                        archlist = archwork.availablearchs(rpmDBInfo,reqname)
                        arch = archwork.bestarch(archlist)
                        (e1, v1, r1) = rpmDBInfo.evr(reqname,arch)
                        (e2, v2, r2) = self.evr(reqname,arch)
                        rc = rpmUtils.compareEVR((e1,v1,r1), (e2,v2,r2))
                        if rc<0:
                            log(4, 'conflict: setting %s to upgrade' % (reqname))
                            self.setPkgState(reqname, arch, 'ud')
                            CheckDeps=1
                        else:
                            errors.append('conflict between %s and %s' % (name, reqname))
                            conflicts=1
                    else:
                        errors.append('conflict between %s and %s' % (name, reqname))
                        conflicts=1
            log(4, 'Restarting Dependency Loop')
            del _ts
            if len(errors) > 0:
                return(1, errors)
