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

import string
import rpm
import os
import os.path
import sys
import gzip
import archwork
import fnmatch
import pkgaction
import callback
import rpmUtils
import time
import urlparse

import urlgrabber
from urlgrabber import close_all, urlgrab, URLGrabError, retrygrab
# it would be nice to make this slurp the REAL version from somewhere :)
urlgrabber.set_user_agent("Yum/2.X")

from i18n import _

def stripENVRA(str):
    archIndex = string.rfind(str, '.')
    arch = str[archIndex+1:]
    relIndex = string.rfind(str[:archIndex], '-')
    rel = str[relIndex+1:archIndex]
    verIndex = string.rfind(str[:relIndex], '-')
    ver = str[verIndex+1:relIndex]
    epochIndex = string.find(str, ':')
    epoch = str[:epochIndex]
    name = str[epochIndex + 1:verIndex]
    return (epoch, name, ver, rel, arch)

def stripEVR(str):
    epochIndex = string.find(str, ':')
    epoch = str[:epochIndex]
    relIndex = string.rfind(str, '-')
    rel = str[relIndex+1:]
    verIndex = string.rfind(str[:relIndex], '-')
    ver = str[epochIndex+1:relIndex]  
    return (epoch, ver, rel)

def stripNA(str):
    archIndex = string.rfind(str, '.')
    arch = str[archIndex+1:]
    name = str[:archIndex]
    return (name, arch)

def getENVRA(header):
    if header[rpm.RPMTAG_EPOCH] == None:
        epoch = '0'
    else:
        epoch = '%s' % header[rpm.RPMTAG_EPOCH]
    name = header[rpm.RPMTAG_NAME]
    ver = header[rpm.RPMTAG_VERSION]
    rel = header[rpm.RPMTAG_RELEASE]
    arch = header[rpm.RPMTAG_ARCH]
    return (epoch, name, ver, rel, arch)

def str_to_version(str):
    i = string.find(str, ':')
    if i != -1:
        epoch = string.atol(str[:i])
    else:
        epoch = '0'
    j = string.find(str, '-')
    if j != -1:
        if str[i + 1:j] == '':
            version = None
        else:
            version = str[i + 1:j]
        release = str[j + 1:]
    else:
        if str[i + 1:] == '':
            version = None
        else:
            version = str[i + 1:]
        release = None
    return (epoch, version, release)

def HeaderInfoNevralLoad(filename, nevral, serverid):
    in_file = open(filename, 'r')
    info = in_file.readlines()
    in_file.close()
    archlist = archwork.compatArchList()
    for line in info:
        (envraStr, rpmpath) = string.split(line, '=')
        (epoch, name, ver, rel, arch) = stripENVRA(envraStr)
        rpmpath = string.replace(rpmpath, '\n', '')
        if arch in archlist:
            if not nameInExcludes(name, serverid):
                if conf.pkgpolicy == 'last':
                    nevral.add((name, epoch, ver, rel, arch, rpmpath, serverid), 'a')
                else:
                    if nevral.exists(name, arch):
                        (e1, v1, r1) = nevral.evr(name, arch)
                        (e2, v2, r2) = (epoch, ver, rel)    
                        rc = rpmUtils.compareEVR((e1, v1, r1), (e2, v2, r2))
                        if (rc < 0):
                            # ooo  the second one is newer - push it in.
                            nevral.add((name, epoch, ver, rel, arch, rpmpath, serverid), 'a')
                    else:
                        nevral.add((name, epoch, ver, rel, arch, rpmpath, serverid), 'a')


def nameInExcludes(name, serverid=None):
    # this function should take a name and check it against the excludes
    # list to see if it shouldn't be in there
    # return true if it is in the Excludes list
    # return false if it is not in the Excludes list
    for exclude in conf.excludes:
        if name == exclude or fnmatch.fnmatch(name, exclude):
            return 1
    if serverid != None: 
        for exclude in conf.serverexclude[serverid]:
            if name == exclude or fnmatch.fnmatch(name, exclude):
                return 1
    return 0

def rpmdbNevralLoad(nevral):
    rpmdbdict = {}
    serverid = 'db'
    rpmloc = 'in_rpm_db'
    hdrs = ts.dbMatch()
    for hdr in hdrs:
        (epoch, name, ver, rel, arch) = getENVRA(hdr)
        # deal with multiple versioned dupes and dupe entries in localdb
        if not rpmdbdict.has_key((name, arch)):
            rpmdbdict[(name, arch)] = (epoch, ver, rel)
        else:
            (e1, v1, r1) = (rpmdbdict[(name, arch)])
            (e2, v2, r2) = (epoch, ver, rel)    
            rc = rpmUtils.compareEVR((e1,v1,r1), (e2,v2,r2))
            if (rc <= -1):
                rpmdbdict[(name, arch)] = (epoch, ver, rel)
            elif (rc == 0):
                log(4, 'dupe entry in rpmdb %s %s' % (name, arch))
    for value in rpmdbdict.keys():
        (name, arch) = value
        (epoch, ver, rel) = rpmdbdict[value]
        nevral.add((name, epoch, ver, rel, arch, rpmloc, serverid), 'n')

def readHeader(rpmfn):
    if string.lower(rpmfn[-4:]) == '.rpm':
        fd = os.open(rpmfn, os.O_RDONLY)
        h = ts.hdrFromFdno(fd)
        os.close(fd)
        if h[rpm.RPMTAG_SOURCEPACKAGE]:
            return 'source'
        else:
            return h
    else:
        try:
            fd = gzip.open(rpmfn, 'r')
            try: 
                h = rpm.headerLoad(fd.read())
            except rpm.error, e:
                errorlog(0,_('Damaged Header %s') % rpmfn)
                return None
        except IOError,e:
            fd = open(rpmfn, 'r')
            try:
                h = rpm.headerLoad(fd.read())
            except rpm.error, e:
                errorlog(0,_('Damaged Header %s') % rpmfn)
                return None
        except ValueError, e:
            return None
    fd.close()
    return h

def returnObsoletes(headerNevral, rpmNevral, uninstNAlist):
    obsoleting = {} # obsoleting[pkgobsoleting]=[list of pkgs it obsoletes]
    obsoleted = {} # obsoleted[pkgobsoleted]=[list of pkgs obsoleting it]
    for (name, arch) in uninstNAlist:
        # DEBUG print '%s, %s' % (name, arch)
        header = headerNevral.getHeader(name, arch)
        obs = header[rpm.RPMTAG_OBSOLETES]
        del header

        # if there is one its a nonversioned obsolete
        # if there are 3 its a versioned obsolete
        # nonversioned are obvious - check the rpmdb if it exists
        # then the pkg obsoletes something in the rpmdb
        # versioned obsolete - obvalue[0] is the name of the pkg
        #                      obvalue[1] is >,>=,<,<=,=
        #                      obvalue[2] is an e:v-r string
        # get the two version strings - labelcompare them
        # get the return value - then run through
        # an if/elif statement regarding obvalue[1] and determine
        # if the pkg obsoletes something in the rpmdb
        if obs:
            for ob in obs:
                obvalue = string.split(ob)
                obspkg = obvalue[0]
                if rpmNevral.exists(obspkg):
                    if len(obvalue) == 1: #unversioned obsolete
                        if not obsoleting.has_key((name, arch)):
                            obsoleting[(name, arch)] = []
                        obsoleting[(name, arch)].append(obspkg)
                        if not obsoleted.has_key(obspkg):
                            obsoleted[obspkg] = []
                        obsoleted[obspkg].append((name, arch))
                        log(4, '%s obsoleting %s' % (name, obspkg))
                    elif len(obvalue) == 3:
                        obscomp = obvalue[1]
                        obsver = obsvalue[2]
                        (e1, v1, r1) = rpmNevral.evr(name, arch)
                        (e2, v2, r2) = str_to_version(obsver)
                        rc = rpmUtils.compareEVR((e1, v1, r1), (e2, v2, r2))
                        if obscomp == '>':
                            if rc >= 1:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                        elif obscomp == '>=':
                            if rc >= 1:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                            elif rc == 0:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                        elif obscomp == '=':
                            if rc == 0:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                        elif obscomp == '<=':
                            if rc == 0:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                            elif rc <= -1:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
                        elif obscomp == '<':
                            if rc <= -1:
                                if not obsoleting.has_key((name, arch)):
                                    obsoleting[(name, arch)] = []
                                obsoleting[(name, arch)].append(obspkg)
                                if not obsoleted.has_key(obspkg):
                                    obsoleted[obspkg] = []
                                obsoleted[obspkg].append((name, arch))
    return obsoleting, obsoleted

def getupdatedhdrlist(headernevral, rpmnevral):
    "returns (name, arch) tuples of updated and uninstalled pkgs"
    uplist = []
    newlist = []
    simpleupdate = []
    complexupdate = []
    uplist_archdict = {}
        # this is all hard and goofy to deal with pkgs changing arch
        # if we have the package installed
        # if the pkg isn't installed then it's a new pkg
        # else
        #   if there isn't more than on available arch from the hinevral 
        #       then compare it to the installed one 
        #   if there is more than one installed or available:
        #   compare highest version and bestarch (in that order of precdence) 
        #   of installed pkgs to highest version and bestarch of available pkgs
        
        # best bet is to chew through the pkgs and throw out the new ones early
        # then deal with the ones where there are a single pkg installed and a 
        # single pkg available
        # then deal with the multiples
        # write a sub-function that takes (nevral, name) and returns list of
        # archs that have the highest version
        
        # additional tricksiness - include a config option to make it ONLY 
        # update identical matches so glibc.i386 can only be updated by 
        # glibc.i386 not by glibc.i686 - this is for the anal and bizare
        
    for (name, arch) in headernevral.NAkeys():
        if not rpmnevral.exists(name):
            newlist.append((name, arch))
        else:
            hdrarchs = archwork.availablearchs(headernevral, name)
            rpmarchs = archwork.availablearchs(rpmnevral, name)
            if len(hdrarchs) > 1 or len(rpmarchs) > 1:
                if name not in complexupdate:
                    log(4, 'putting %s in complex update list' % name)
                    complexupdate.append(name)
            else:
                log(4, 'putting %s in simple update list' % name)
                simpleupdate.append((name, arch))
    # we have our lists to work with now

    # simple cases
    for (name, arch) in simpleupdate:
        # try to be as precise as possible
        if conf.exactarch:
            # make the default case false
            exactmatch = 0
        else:
            # make the default case true
            exactmatch = 1
        
        if rpmnevral.exists(name, arch):
            exactmatch = 1
            (rpm_e, rpm_v, rpm_r) = rpmnevral.evr(name, arch)
        else:
            (rpm_e, rpm_v, rpm_r) = rpmnevral.evr(name)
            
        if exactmatch:
            rc = rpmUtils.compareEVR(headernevral.evr(name), (rpm_e, rpm_v, rpm_r))
            if rc > 0:
                uplist.append((name, arch))
        
    # complex cases
    for name in complexupdate:
        hdrarchs = bestversion(headernevral, name)
        rpmarchs = bestversion(rpmnevral, name)
        hdr_best_arch = archwork.bestarch(hdrarchs)
        log(5, 'Best ver+arch avail for %s is %s' % (name, hdr_best_arch))
        rpm_best_arch = archwork.bestarch(rpmarchs)
        log(5, 'Best ver+arch installed for %s is %s' % (name, rpm_best_arch))
        
        # dealing with requests to only update exactly what is installed
        # we clearly want to update the stuff that is installed
        # and compare it to best that is available - but only if the arch 
        # matches so we go through the lists of rpmarchs of bestversion, check 
        # for them in hdrarchs - if they are there compare them and mark them
        # accordingly
        # if for some reason someone has two pkgs of the same version but 
        # different arch installed and we happen to have both available then
        # they'll get both - if anyone can point out a situation when this is 
        # "legal" let me know.

        if conf.exactarch:
            for arch in rpmarchs:
                if arch in hdrarchs:
                    log(5, 'Exact match in complex for %s - %s' % (name, arch))
                    rc = rpmUtils.compareEVR(headernevral.evr(name, arch), rpmnevral.evr(name, arch))
                    if rc > 0:
                        uplist.append((name, arch))
                else:
                    log(5, 'Inexact match in complex for %s - %s' % (name, arch))
        else:
            rc = rpmUtils.compareEVR(headernevral.evr(name, hdr_best_arch), rpmnevral.evr(name, rpm_best_arch))
            if rc > 0:
                uplist.append((name, hdr_best_arch))

    nulist=uplist+newlist
    return (uplist, newlist, nulist)


def bestversion(nevral, name):
    """this takes a nevral and a pkg name - it iterates through them to return
       the list of archs having the highest version number - so if someone has
       package foo.i386 and foo.i686 then we'll get a list of i386 and i686 returned
       minimum of one thing returned"""
    # first we get a list of the archs
    # determine the best e-v-r
    # then we determine the archs that have that version and append them to a list
    returnarchs = []
    
    archs = archwork.availablearchs(nevral, name)
    currentarch = archs[0]
    for arch in archs[1:]:
        rc = rpmUtils.compareEVR(nevral.evr(name, currentarch), nevral.evr(name, arch))
        if rc < 0:
            currentarch = arch
        elif rc == 0:
            pass
        elif rc > 0:
            pass
    (best_e, best_v, best_r) = nevral.evr(name, currentarch)
    log(3, 'Best version for %s is %s:%s-%s' % (name, best_e, best_v, best_r))
    
    for arch in archs:
        rc = rpmUtils.compareEVR(nevral.evr(name, arch), (best_e, best_v, best_r))
        if rc == 0:
            returnarchs.append(arch)
        elif rc > 0:
            log(4, 'What the hell, we just determined it was the bestversion')
    
    log(7, returnarchs)
    return returnarchs
    
def formatRequire (name, version, flags):
    string = name
        
    if flags:
        if flags & (rpm.RPMSENSE_LESS | rpm.RPMSENSE_GREATER | rpm.RPMSENSE_EQUAL):
            string = string + ' '
        if flags & rpm.RPMSENSE_LESS:
            string = string + '<'
        if flags & rpm.RPMSENSE_GREATER:
            string = string + '>'
        if flags & rpm.RPMSENSE_EQUAL:
            string = string + '='
            string = string + ' %s' % version
    return string


def actionslists(nevral):
    install_list = []
    update_list = []
    erase_list = []
    updatedeps_list = []
    erasedeps_list = []
    for (name, arch) in nevral.NAkeys():
        if nevral.state(name, arch) in ('i', 'iu'):
            install_list.append((name, arch))
        if nevral.state(name, arch) == 'u':
            update_list.append((name, arch))
        if nevral.state(name, arch) == 'e':
            erase_list.append((name, arch))
        if nevral.state(name, arch) == 'ud':
            updatedeps_list.append((name, arch))
        if nevral.state(name, arch) == 'ed':
            erasedeps_list.append((name, arch))
    
    return install_list, update_list, erase_list, updatedeps_list, erasedeps_list
    
def printactions(i_list, u_list, e_list, ud_list, ed_list, nevral):
    log(2, _('I will do the following:'))
    for pkg in i_list:
        (name,arch) = pkg
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        log(2, _('[install: %s]') % pkgstring)
    for pkg in u_list:
        (name,arch) = pkg
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        log(2, _('[update: %s]') % pkgstring)
    for pkg in e_list:
        (name,arch) = pkg
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        log(2, _('[erase: %s]') % pkgstring)
    if len(ud_list) > 0:
        log(2, _('I will install/upgrade these to satisfy the dependencies:'))
        for pkg in ud_list:
            (name, arch) = pkg
            (e, v, r) = nevral.evr(name, arch)
            pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
            log(2, _('[deps: %s]') % pkgstring)
    if len(ed_list) > 0:
        log(2, 'I will erase these to satisfy the dependencies:')
        for pkg in ed_list:
            (name, arch) = pkg
            (e, v, r) = nevral.evr(name, arch)
            pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
            log(2, _('[deps: %s]') % pkgstring)

def filelogactions(i_list, u_list, e_list, ud_list, ed_list, nevral):
    i_log = 'Installed: '
    ud_log = 'Dep Installed: '
    u_log = 'Updated: '
    e_log = 'Erased: '
        
    for (name, arch) in i_list:
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        filelog(1, i_log + pkgstring)
    for (name, arch) in ud_list:
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        filelog(1, ud_log + pkgstring)
    for (name, arch) in u_list:
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        filelog(1, u_log + pkgstring)
    for (name, arch) in e_list+ed_list:
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        filelog(1, e_log + pkgstring)
        

def shortlogactions(i_list, u_list, e_list, ud_list, ed_list, nevral):
    i_log = 'Installed: '
    ud_log = 'Dep Installed: '
    u_log = 'Updated: '
    e_log = 'Erased: '
    
    for (name, arch) in i_list:
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        i_log = i_log + ' ' + pkgstring
    for (name, arch) in ud_list:
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        ud_log = ud_log + ' ' + pkgstring
    for (name, arch) in u_list:
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        u_log = u_log + ' ' + pkgstring
    for (name, arch) in e_list+ed_list:
        (e, v, r) = nevral.evr(name, arch)
        pkgstring = '%s:%s-%s-%s.%s' % (e, name, v, r, arch)
        e_log = e_log + ' ' + pkgstring

    if len(i_list) > 0:
        log(1, i_log)
    if len(u_list) > 0:
        log(1, u_log)
    if len(ud_list) > 0:
        log(1, ud_log)
    if len(e_list+ed_list) > 0:
        log(1, e_log)
        


def userconfirm():
    """gets a yes or no from the user, defaults to No"""
    choice = raw_input('Is this ok [y/N]: ')
    if len(choice) == 0:
        return 1
    else:
        if choice[0] != 'y' and choice[0] != 'Y':
            return 1
        else:
            return 0
        


def nasort((n1, a1), (n2, a2)):
    if n1 > n2:
        return 1
    elif n1 == n2:
        return 0
    else:
        return -1
        
def getfilelist(path, ext, list):
    # get all the files matching the 3 letter extension that is ext in path, 
    # recursively
    # append them to list
    # return list
    # ignore symlinks
    dir_list = os.listdir(path)
    for d in dir_list:
        if os.path.isdir(path + '/' + d):
            list = getfilelist(path + '/' + d, ext, list)
        else:
            if string.lower(d[-4:]) == '%s' % (ext):
                if not os.path.islink( path + '/' + d): 
                    newpath = os.path.normpath(path + '/' + d)
                    list.append(newpath)
    return(list)

def clean_up_headers():
    serverlist = conf.servers
    for serverid in serverlist:
        hdrdir = conf.serverhdrdir[serverid]
        hdrlist = getfilelist(hdrdir, '.hdr', [])
        # remove header.info file too
        headerinfofile = os.path.join(conf.cachedir, serverid, 'header.info')
        log(4, 'Deleting header.info for %s' % serverid)
        os.unlink(headerinfofile)
        for hdr in hdrlist:
            log(4, 'Deleting Header %s' % hdr)
            os.unlink(hdr)
            

def clean_up_packages():
    serverlist = conf.servers
    for serverid in serverlist:
        rpmdir = conf.serverpkgdir[serverid]
        rpmlist = getfilelist(rpmdir, '.rpm', [])
        for rpm in rpmlist:
            log(4, 'Deleting Package %s' % rpm)
            os.unlink(rpm)
    

def clean_up_old_headers(rpmDBInfo, HeaderInfo):
    serverlist = conf.servers
    hdrlist = []
    for serverid in serverlist:
        hdrdir = conf.serverhdrdir[serverid]
        hdrlist = getfilelist(hdrdir, '.hdr', hdrlist)
    for hdrfn in hdrlist:
        hdr = readHeader(hdrfn)
        (e, n, v, r, a) = getENVRA(hdr)
        if rpmDBInfo.exists(n, a):
            (e1, v1, r1) = rpmDBInfo.evr(n, a)
            rc = rpmUtils.compareEVR((e1, v1, r1), (e, v, r))
            # if the rpmdb has an equal or better rpm then delete
            # the header
            if (rc >= 0):
                log(5, 'Deleting Header %s' % hdrfn)
                try:
                    os.unlink(hdrfn)
                except OSError, e:
                    errorlog(2, 'Attempt to delete a missing file %s - ignoring.' % hdrfn)
        if not HeaderInfo.exists(n, a):
            # if its not in the HeaderInfo nevral anymore just kill it
            log(5, 'Deleting Header %s' % hdrfn)
            try:
                os.unlink(hdrfn)
            except OSError, e:
                errorlog(2, 'Attempt to delete a missing file %s - ignoring.' % hdrfn)
            

def printtime():
    return time.strftime('%m/%d/%y %H:%M:%S ', time.localtime(time.time()))

def get_groups_from_servers(serveridlist):
    """takes a list of serverids - returns a list of servers that either:
       gave us yumcomps.xml or for whom we had a cached one"""
       
    log(2, 'Getting groups from servers')
    validservers = []
    for serverid in serveridlist:
        remotegroupfile = conf.remoteGroups(serverid)
        localgroupfile = conf.localGroups(serverid)
        if not conf.cache:
            log(3, 'getting groups from server: %s' % serverid)
            try:
                localgroupfile = grab(serverid, remotegroupfile, localgroupfile, nofail=1, copy_local=1)
            except URLGrabError, e:
                log(3, 'Error getting file %s' % remotegroupfile)
                log(3, '%s' % e)
        else:
            if os.path.exists(localgroupfile):
                log(2, 'using cached groups from server: %s' % serverid)
        if os.path.exists(localgroupfile):
            log(3, 'Got a file - yay')
            validservers.append(serverid)
    return validservers
        
def get_package_info_from_servers(serveridlist, HeaderInfo):
    """gets header.info from each server if it can, checks it, if it can, then
       builds the list of available pkgs from there by handing each headerinfofn
       to HeaderInfoNevralLoad()"""
    log(2, 'Gathering header information file(s) from server(s)')
    for serverid in serveridlist:
        servername = conf.servername[serverid]
        serverheader = conf.remoteHeader(serverid)
        servercache = conf.servercache[serverid]
        log(2, 'Server: %s' % (servername))
        log(4, 'CacheDir: %s' % (servercache))
        localpkgs = conf.serverpkgdir[serverid]
        localhdrs = conf.serverhdrdir[serverid]
        localheaderinfo = conf.localHeader(serverid)
        if not os.path.exists(servercache):
            os.mkdir(servercache)
        if not os.path.exists(localpkgs):
            os.mkdir(localpkgs)
        if not os.path.exists(localhdrs):
            os.mkdir(localhdrs)
        if not conf.cache:
            log(3, 'Getting header.info from server')
            try:
                headerinfofn = grab(serverid, serverheader, localheaderinfo, copy_local=1)
            except URLGrabError, e:
                errorlog(0, 'Error getting file %s' % serverheader)
                errorlog(0, '%s' % e)
                sys.exit(1)
        else:
            if os.path.exists(localheaderinfo):
                log(3, 'Using cached header.info file')
                headerinfofn = localheaderinfo
            else:
                errorlog(0, 'Error - %s cannot be found' % localheaderinfo)
                if conf.uid != 0:
                    errorlog(1, 'Please ask your sysadmin to update the headers on this system.')
                else:
                    errorlog(1, 'Please run yum in non-caching mode to correct this header.')
                sys.exit(1)
        log(4,'headerinfofn: ' + headerinfofn)
        HeaderInfoNevralLoad(headerinfofn, HeaderInfo, serverid)

def download_headers(HeaderInfo, nulist):
    total = len(nulist)
    current = 1
    for (name, arch) in nulist:
        LocalHeaderFile = HeaderInfo.localHdrPath(name, arch)
        RemoteHeaderFile = HeaderInfo.remoteHdrUrl(name, arch)
        
        serverid = HeaderInfo.serverid(name, arch)
        # if we have one cached, check it, if it fails, unlink it and continue
        # as if it never existed
        # else move along
        if os.path.exists(LocalHeaderFile):
            log(5, 'checking cached header: %s' % LocalHeaderFile)
            try:
                rpmUtils.checkheader(LocalHeaderFile, name, arch)
            except URLGrabError, e:
                if conf.cache:
                    errorlog(0, 'The file %s is damaged.' % LocalHeaderFile)
                    if conf.uid != 0:
                        errorlog(1, 'Please ask your sysadmin to update the headers on this system.')
                    else:
                        errorlog(1, 'Please run yum in non-caching mode to correct this header.')
                    sys.exit(1)
                else:
                    os.unlink(LocalHeaderFile)
            else:
                continue
                
        if not conf.cache:
            log(2, 'getting %s' % (LocalHeaderFile))
            try:
                hdrfn = grab(serverid, RemoteHeaderFile, LocalHeaderFile, copy_local=1,
                                  checkfunc=(rpmUtils.checkheader, (name, arch), {}))
            except URLGrabError, e:
                errorlog(0, 'Error getting file %s' % RemoteHeaderFile)
                errorlog(0, '%s' % e)
                sys.exit(1)
            HeaderInfo.setlocalhdrpath(name, arch, hdrfn)
        else:
            errorlog(1, 'Cannot download %s in caching only mode or when running as non-root user.' % RemoteHeaderFile)
            sys.exit(1)
        current = current + 1
    close_all()
                
def take_action(cmds, nulist, uplist, newlist, obsoleting, tsInfo, HeaderInfo, rpmDBInfo, obsoleted):
    from yummain import usage
    
    basecmd = cmds.pop(0)
    
    if conf.uid != 0:
        if basecmd in ['install','update','clean','upgrade','erase', 'groupupdate', 'groupupgrade', 'groupinstall']:
            errorlog(0, _('You need to be root to perform these commands'))
            sys.exit(1)
    
    if basecmd == 'install':
        if len(cmds) == 0:
            errorlog(0, _('Need to pass a list of pkgs to install'))
            usage()
        else:
            if conf.tolerant:
                pkgaction.installpkgs(tsInfo, nulist, cmds, HeaderInfo, rpmDBInfo, 0)
            else: 
                pkgaction.installpkgs(tsInfo, nulist, cmds, HeaderInfo, rpmDBInfo, 1)
                
    elif basecmd == 'provides':
        if len(cmds) == 0:
            errorlog(0, _('Need a provides to match'))
            usage()
        else:
            log(2, _('Looking in available packages for a providing package'))
            pkgaction.whatprovides(cmds, nulist, HeaderInfo,0)
            log(2, _('Looking in installed packages for a providing package'))
            pkgaction.whatprovides(cmds, nulist, rpmDBInfo,1)
        sys.exit(0)
    
    elif basecmd == 'update':
        if len(cmds) == 0:
            pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, 'all', 0)
        else:
            if conf.tolerant:
                pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, cmds, 0)
            else:
                pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, cmds, 1)
            
    elif basecmd == 'upgrade':
        if len(cmds) == 0:
            pkgaction.upgradepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, obsoleted, obsoleting, 'all', 0)
        else:
            if conf.tolerant:
                pkgaction.upgradepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, obsoleted, obsoleting, cmds, 1)
            else:
                pkgaction.upgradepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, obsoleted, obsoleting, cmds, 0)
    
    elif basecmd in ('erase', 'remove'):
        if len(cmds) == 0:
            usage()
            errorlog (0, _('Need to pass a list of pkgs to erase'))
        else:
            if conf.tolerant:
                pkgaction.erasepkgs(tsInfo, rpmDBInfo, cmds, 0)
            else:
                pkgaction.erasepkgs(tsInfo, rpmDBInfo, cmds, 1)
    
    elif basecmd == 'check-update':
        if len(uplist) > 0:
            pkgaction.listpkginfo(uplist, 'all', HeaderInfo, 1)
            sys.exit(100)
        else:
            sys.exit(0)
            
    elif basecmd in ['list', 'info']:
        if basecmd == 'list':
            short = 1
        else:
            short = 0
        if len(cmds) == 0:
            pkgaction.listpkginfo(nulist, 'all', HeaderInfo, short)
            sys.exit(0)
        else:
            if cmds[0] == 'updates':
                pkgaction.listpkginfo(uplist, 'updates', HeaderInfo, short)
            elif cmds[0] == 'available':
                pkgaction.listpkginfo(newlist, 'all', HeaderInfo, short)
            elif cmds[0] == 'installed':
                pkglist = rpmDBInfo.NAkeys()
                pkgaction.listpkginfo(pkglist, 'all', rpmDBInfo, short)
            elif cmds[0] == 'extras':
                pkglist=[]
                for (name, arch) in rpmDBInfo.NAkeys():
                    if not HeaderInfo.exists(name, arch):
                        pkglist.append((name,arch))
                if len(pkglist) > 0:
                    pkgaction.listpkginfo(pkglist, 'all', rpmDBInfo, short)
                else:
                    log(2, _('No Packages installed not included in a repository'))
            else:    
                log(2, _('Looking in Available Packages:'))
                pkgaction.listpkginfo(nulist, cmds, HeaderInfo, short)
                log(2, _('Looking in Installed Packages:'))
                pkglist = rpmDBInfo.NAkeys()
                pkgaction.listpkginfo(pkglist, cmds, rpmDBInfo, short)
        sys.exit(0)
    elif basecmd == 'grouplist':
        pkgaction.listgroups(cmds)
        sys.exit(0)
    
    elif basecmd == 'groupupdate':
        if len(cmds) == 0:
            errorlog(0, _('Need a list of groups to update'))
            sys.exit(1)
        installs, updates = pkgaction.updategroups(rpmDBInfo, nulist, uplist, cmds)
        if len(updates) > 0:
            pkglist = []
            for (group, pkg) in updates:
                pkglist.append(pkg)
            pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, pkglist, 0)
        if len(installs) > 0:
            pkglist = []
            for (group, pkg) in installs:
                pkglist.append(pkg)
            pkgaction.installpkgs(tsInfo, nulist, pkglist, HeaderInfo, rpmDBInfo, 0)
            
    elif basecmd == 'groupinstall':
        if len(cmds) == 0:
            errorlog(0, _('Need a list of groups to update'))
            sys.exit(1)
        instpkglist = pkgaction.installgroups(rpmDBInfo, nulist, uplist, cmds)
        if len(instpkglist) > 0:
            pkgaction.installpkgs(tsInfo, nulist, instpkglist, HeaderInfo, rpmDBInfo, 0)
        
            
    elif basecmd == 'clean':
        if len(cmds) == 0 or cmds[0] == 'all':
            log(2, _('Cleaning packages and old headers'))
            clean_up_packages()
            clean_up_old_headers(rpmDBInfo, HeaderInfo)
        elif cmds[0] == 'packages':
            log(2, _('Cleaning packages'))
            clean_up_packages()
        elif cmds[0] == 'headers':
            log(2, _('Cleaning all headers'))
            clean_up_headers()
        elif cmds[0] == 'oldheaders':
            log(2, _('Cleaning old headers'))
            clean_up_old_headers(rpmDBInfo, HeaderInfo)
        else:
            errorlog(0, _('Invalid clean option %s') % cmds[0])
            sys.exit(1)
        sys.exit(0)    
    else:
        usage()

def create_final_ts(tsInfo):
    # download the pkgs to the local paths and add them to final transaction set
    tsfin = rpm.TransactionSet(conf.installroot)
    for (name, arch) in tsInfo.NAkeys():
        pkghdr = tsInfo.getHeader(name, arch)
        rpmloc = tsInfo.localRpmPath(name, arch)
        serverid = tsInfo.serverid(name, arch)
        state = tsInfo.state(name, arch)
        if state in ('u', 'ud', 'iu', 'i'): # inst/update
            # this should be just like the hdr getting
            # check it out- if it is good, move along
            # otherwise, download, check, wash, rinse, repeat
            # just md5/open check - we'll mess with gpg checking after we know
            # that the pkg is valid
            if os.path.exists(rpmloc):
                log(4, 'Checking cached RPM %s' % (os.path.basename(rpmloc)))
                if not rpmUtils.checkRpmMD5(rpmloc):
                    errorlog(0, 'Damaged RPM %s, removing.' % (rpmloc))
                    os.unlink(rpmloc)
                else:
                    rpmobj = rpmUtils.RPM_Work(rpmloc)
                    hdre = pkghdr['epoch']
                    hdrv = pkghdr['version']
                    hdrr = pkghdr['release']
                    (rpme, rpmv, rpmr) = rpmobj.evr()
                    if (rpme, rpmv, rpmr) != (hdre, hdrv, hdrr):
                        errorlog(0, 'NonMatching RPM version, %s, removing.' %(rpmloc))
                        os.unlink(rpmloc)

            # gotten rid of the bad ones
            # now lets download things
            if os.path.exists(rpmloc):
                pass
            else:
                log(2, 'Getting %s' % (os.path.basename(rpmloc)))
                remoterpmurl = tsInfo.remoteRpmUrl(name, arch)
                try:
                    localrpmpath = grab(serverid, remoterpmurl, rpmloc, copy_local=0,
                                             checkfunc=(rpmUtils.checkRpmMD5, (), {'urlgraberror':1})) 
                except URLGrabError, e:
                    errorlog(0, 'Error getting file %s' % remoterpmurl)
                    errorlog(0, '%s' % e)
                    sys.exit(1)
                else:
                    tsInfo.setlocalrpmpath(name, arch, localrpmpath)
                    
            # we now actually have the rpm and we know where it is - so use it
            rpmloc = tsInfo.localRpmPath(name, arch)
            if conf.servergpgcheck[serverid]:
                rc = rpmUtils.checkSig(rpmloc, serverid)
                if rc == 1:
                    errorlog(0, _('Error: Could not find the GPG Key necessary to validate pkg %s') % rpmloc)
                    errorlog(0, _('Error: You may want to run yum clean or remove the file: \n %s') % rpmloc)
                    errorlog(0, _('Error: You may also check that you have the correct GPG keys installed'))
                    sys.exit(1)
                elif rc == 2:
                    errorlog(0, _('Error Reading Header on %s') % rpmloc)
                    errorlog(0, _('Error: You may want to run yum clean or remove the file: \n %s') % rpmloc)
                    sys.exit(1)
                elif rc == 3:
                    errorlog(0, _('Error: Untrusted GPG key on %s') % rpmloc)
                    errorlog(0, _('Error: You may want to run yum clean or remove the file: \n %s') % rpmloc)
                    errorlog(0, _('Error: You may also check that you have the correct GPG keys installed'))
                    sys.exit(1)
            if state == 'i':
                tsfin.addInstall(pkghdr, (pkghdr, rpmloc), 'i')
            else:
                tsfin.addInstall(pkghdr, (pkghdr, rpmloc), 'u')
        elif state == 'a':
            pass
        elif state == 'e' or state == 'ed':
            tsfin.addErase(name)
    close_all()
    return tsfin

def diskspacetest(diskcheckts):
    diskcheckts.setFlags(rpm.RPMTRANS_FLAG_TEST)
    diskcheckts.setProbFilter(~rpm.RPMPROB_FILTER_DISKSPACE)
    cb = callback.RPMInstallCallback()
    tserrors = diskcheckts.run(cb.callback, '')
    if tserrors:
        diskerrors = []
        othererrors = []
        for (descr, (type, mount, need)) in tserrors:
            if type == rpm.RPMPROB_DISKSPACE:
                diskerrors.append(descr)
            else:
                othererrors.append(descr)
        if len(diskerrors) > 0:
            log(2, 'Error: Disk space Error')
            errorlog(0, 'You appear to have insufficient disk space to handle these packages')
            for error in diskerrors:
                errorlog(1, '%s' % error)
        if len(othererrors) > 0:
            log(2, 'Error reported but not a disk space error')
            errorlog(0, 'Unknown error testing transaction set:')
            for error in othererrors:
                errorlog(1, '%s' % error)
        sys.exit(1)


def descfsize(size):
    """The purpose of this function is to accept a file size in bytes,
    and describe it in a human readable fashion."""
    if size < 1000:
        return "%d bytes" % size
    elif size < 1000000:
        size = size / 1000.0
        return "%.2f kB" % size
    elif size < 1000000000:
        size = size / 1000000.0
        return "%.2f MB" % size
    else:
        size = size / 1000000000.0
        return "%.2f GB" % size

def grab(serverID, url, filename=None, nofail=0, copy_local=0, 
          close_connection=0,
          progress_obj=None, throttle=None, bandwidth=None,
          numtries=3, retrycodes=[-1,2,4,5,6,7], checkfunc=None):

    """Wrap retry grab and add in failover stuff.  This needs access to
    the conf class as well as the serverID.

    nofail -- Set to true to go through the failover object without
       incrementing the failures counter.  (Actualy this just resets
       the failures counter.)  Usefull in the yumgroups.xml special case.

    We do look at retrycodes here to see if we should return or failover.
    On fail we will raise the last exception that we got."""

    fc = conf.get_failClass(serverID)
    base = ''
    findex = fc.get_index()
    
    for root in conf.serverurl[serverID]:
        if string.find(url, root) == 0:
            # We found the current base this url is made of
            base = root
            break
    if base == '':
        # We didn't find the base...something is wrong
        raise Exception, "%s isn't made from a base URL I know about" % url
    filepath = url[len(base):]
    log(3, "failover: baseURL = " + base)
    log(3, "failover: path = " + filepath)

    # don't trust the base that the user supplied
    # this call will return the same thing as fc.get_serverurl(findex)
    base = fc.get_serverurl()
    while base != None:
        # Loop over baseURLs until one works or all are dead
        try:
            (scheme, host, path, parm, query, frag) = urlparse.urlparse(base)
            path = os.path.normpath(path + '/' + filepath)
            finalurl = urlparse.urlunparse((scheme, host, path, parm, query, frag))
            return retrygrab(finalurl, filename, copy_local,
                             close_connection, progress_obj, throttle,
                             bandwidth, conf.retries, retrycodes, checkfunc)
            # What?  We were successful?
        except URLGrabError, e:
            if e.errno in retrycodes:
                errorlog(1, "retrygrab() failed for:\n  %s%s\n  Executing failover method" % (base, filepath))
                if nofail:
                    findex = findex + 1
                    base = fc.get_serverurl(findex)
                else:
                    fc.server_failed()
                    base = fc.get_serverurl()
                if base == None:
                    if not nofail:
                        errorlog(1, "failover: out of servers to try")
                    raise
            else:
                raise
