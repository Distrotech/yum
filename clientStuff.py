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

import urllib2
import urlparse
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


try:
    # This is a convenient way to make keepalive optional.
    # Just rename the module so it can't be imported.
    from keepalive import HTTPHandler
    keepalive_handler = HTTPHandler()
    opener = urllib2.build_opener(keepalive_handler)
    urllib2.install_opener(opener)
except ImportError, msg:
    keepalive_handler = None

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
            if not nameInExcludes(name):
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


def nameInExcludes(name):
    # this function should take a name and check it against the excludes list to see if it
    # shouldn't be in there
    # return true if it is in the Excludes list
    # return false if it is not in the Excludes list
    for exclude in conf.excludes:
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
    obsdict = {} # obsdict[obseletinglist]=packageitobsoletes
    for (name, arch) in uninstNAlist:
        # DEBUG print '%s, %s' % (name, arch)
        header = headerNevral.getHeader(name, arch)
        obs = header[rpm.RPMTAG_OBSOLETES]
        del header
        if obs:
        # DEBUG print "%s, %s obs something" % (name, arch)
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
            for ob in obs:
                obvalue = string.split(ob)
                if rpmNevral.exists(obvalue[0]):
                    if len(obvalue) == 1:
                        obsdict[(name, arch)]=obvalue[0]
                        log(4, '%s obsoleting %s' % (name, ob))
                    elif len(obvalue) == 3:
                        (e1, v1, r1) = rpmNevral.evr(name, arch)
                        (e2, v2, r2) = str_to_version(obvalue[3])
                        rc = rpmUtils.compareEVR((e1, v1, r1), (e2, v2, r2))
                        if obvalue[2] == '>':
                            if rc >= 1:
                                obsdict[(name, arch)]=obvalue[0]
                            elif rc == 0:
                                pass
                            elif rc <= -1:
                                pass
                        elif obvalue[2] == '>=':
                            if rc >= 1:
                                obsdict[(name, arch)]=obvalue[0]
                            elif rc == 0:
                                obsdict[(name, arch)]=obvalue[0]
                            elif rc <= -1:
                                pass
                        elif obvalue[2] == '=':
                            if rc >= 1:
                                pass
                            elif rc == 0:
                                obsdict[(name, arch)]=obvalue[0]
                            elif rc <= -1:
                                pass
                        elif obvalue[2] == '<=':
                            if rc >= 1:
                                pass
                            elif rc == 0:
                                obsdict[(name, arch)]=obvalue[0]
                            elif rc <= -1:
                                obsdict[(name, arch)]=obvalue[0]
                        elif obvalue[2] == '<':
                            if rc >= 1:
                                pass
                            elif rc == 0:
                                pass
                            elif rc <= -1:
                                obsdict[(name, arch)]=obvalue[0]
    return obsdict

def progresshook(blocks, blocksize, total):
    totalblocks = total/blocksize
    curbytes = blocks*blocksize
    sys.stdout.write('\r' + ' ' * 80)
    sys.stdout.write('\rblock: %d/%d' % (blocks, totalblocks))
    sys.stdout.flush()
    if curbytes == total:
        print ' '
        

def urlgrab(url, filename=None, copy_local=0, close_connection=0):
    """grab the file at <url> and make a local copy at <filename>

    If filename is none, the basename of the url is used.

    copy_local is ignored except for file:// urls, in which case it
    specifies whether urlgrab should still make a copy of the file, or
    simply point to the existing copy.

    close_connection tells urlgrab to close the connection after
    completion.  This is ignored unless the download happends with the
    http keepalive handler.  Otherwise, the connection is left open
    for further use.

    urlgrab returns the filename of the local file.
    """

    (scheme,host, path, parm, query, frag) = urlparse.urlparse(url)
    path = os.path.normpath(path)
    url = urlparse.urlunparse((scheme, host, path, parm, query, frag))

    if filename == None:
        filename = os.path.basename(path)
    if scheme == 'file' and not copy_local:
        # just return the name of the local file - don't make a copy
        if os.path.isfile(path):
            return path
        else:
            errorlog(0, 'Not a normal file: %s' % path)
            errorlog(0, 'URL: %s' % url)
            sys.exit(1)

    # now fetch the file
    try:
        fo = urllib2.urlopen(url)
        _do_grab(filename, fo)
        hdr = fo.info()
        fo.close()
        if close_connection:
            # try and close connection
            try: fo.close_connection()
            except AttributeError: pass
    except IOError, e:
        errorlog(0, _('IOError: %s')  % (e))
        errorlog(0, _('URL: %s') % (url))
        sys.exit(1)

    # this is a cute little hack - if there isn't a "Content-Length"
    # header then its probably something generated dynamically, such
    # as php, cgi, a directory listing, or an error message.  It is
    # probably not what we want.
    if not hdr is None and not hdr.has_key('Content-Length'):
        errorlog(0, _('ERROR: Url Return no Content-Length  - something is wrong'))
        errorlog(0, _('URL: %s') % (url))
        sys.exit(1)
    return filename

def _do_grab(filename, fo):
    new_fo = open(filename, 'wb')
    bs = 1024*8
    block = fo.read(bs)
    while block:
        new_fo.write(block)
        block = fo.read(bs)
    new_fo.close()

def getupdatedhdrlist(headernevral, rpmnevral):
    "returns (name, arch) tuples of updated and uninstalled pkgs"
    uplist = []
    newlist = []
    uplist_archdict = {}
    for (name, arch) in headernevral.NAkeys():
        # this is all hard and goofy to deal with pkgs changing arch
        # if we have the package installed
        # check to see if we have that specific arch
        # if so compare that name,arch vs the bestarch in the rpmdb 
        # this deals with us having 2.4.9-31.i686 kernels installed AND 2.4.18-4.athlon kernels installed
        # b/c a 2.4.18-4.i686 would constantly show up on an athlon
        # if its newer then mark it as updateable
        # if we don't have that specific arch, then if its the best arch in the headernevral, compare
        # it to what we have, if its newer then mark it as updateable
        if rpmnevral.exists(name):
            if rpmnevral.exists(name, arch):
                archlist = archwork.availablearchs(rpmnevral, name)
                #comparison needs to be done here to find out
                # 1.which of the name+arch combos is the newest (independent of arch)
                # 2.if any two are the same version then what arch is the best)
                bestarch = archwork.bestarch(archlist)
                rc = rpmUtils.compareEVR(headernevral.evr(name, arch), rpmnevral.evr(name, bestarch))
                if (rc > 0):
                    if not uplist_archdict.has_key(name):
                        uplist_archdict[name]=bestarch
                    else:
                        rc = rpmUtils.compareEVR(headernevral.evr(name, bestarch), headernevral.evr(name, uplist_archdict[name]))
                        if (rc > 0):
                            finalarch = archwork.bestarch([bestarch, uplist_archdict[name]])
                            if finalarch == bestarch:
                                 uplist_archdict[name]=bestarch                 
            else:
                archlist = archwork.availablearchs(headernevral, name)
                bestarch = archwork.bestarch(archlist)
                if arch == bestarch:
                    rpmarchlist = archwork.availablearchs(rpmnevral, name)
                    bestrpmarch = archwork.bestarch(rpmarchlist)
                    rc = rpmUtils.compareEVR(headernevral.evr(name, arch), rpmnevral.evr(name, bestrpmarch))
                    if (rc > 0):
                        if not uplist_archdict.has_key(name):
                            uplist_archdict[name]=bestarch
                        else:
                            rc = rpmUtils.compareEVR(headernevral.evr(name, bestarch), headernevral.evr(name, uplist_archdict[name]))
                            if (rc > 0):
                                finalarch = archwork.bestarch([bestarch, uplist_archdict[name]])
                                if finalarch == bestarch:
                                     uplist_archdict[name]=bestarch                 
        else:
            newlist.append((name, arch))

    for name in uplist_archdict.keys():
        uplist.append((name,uplist_archdict[name]))

    nulist=uplist+newlist
    return (uplist, newlist, nulist)



    
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
    
def printactions(i_list, u_list, e_list, ud_list, ed_list):
    log(2, _('I will do the following:'))
    for pkg in i_list:
        (name,arch) = pkg
        log(2, _('[install: %s.%s]') % (name, arch))
    for pkg in u_list:
        (name,arch) = pkg
        log(2, _('[update: %s.%s]') % (name, arch))
    for pkg in e_list:
        (name,arch) = pkg
        log(2, _('[erase: %s.%s]') % (name, arch))
    if len(ud_list) > 0:
        log(2, _('I will install/upgrade these to satisfy the depedencies:'))
        for pkg in ud_list:
            (name, arch) = pkg
            log(2, _('[deps: %s.%s]') %(name, arch))
    if len(ed_list) > 0:
        log(2, 'I will erase these to satisfy the depedencies:')
        for pkg in ed_list:
            (name, arch) = pkg
            log(2, '[deps: %s.%s]' %(name, arch))

def filelogactions(i_list, u_list, e_list, ud_list, ed_list):
    i_log = 'Installed: '
    u_log = 'Updated: '
    e_log = 'Erased: '
        
    for (name, arch) in i_list:
        filelog(1, i_log + name + '.' + arch)
    for (name, arch) in u_list+ud_list:
        filelog(1, u_log + name + '.' + arch)
    for (name, arch) in e_list+ed_list:
        filelog(1, e_log + name + '.' + arch)
        

def shortlogactions(i_list, u_list, e_list, ud_list, ed_list):
    i_log = 'Installed: '
    u_log = 'Updated: '
    e_log = 'Erased: '
    
    for (name, arch) in i_list:
        i_log=i_log + ' ' + name + '.' + arch
    for (name, arch) in u_list+ud_list:
        u_log=u_log + ' ' + name + '.' + arch
    for (name, arch) in e_list+ed_list:
        e_log=e_log + ' ' + name + '.' + arch
    if len(i_list) > 0:
        log(1, i_log)
    if len(u_list+ud_list) > 0:
        log(1, u_log)
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
    # get all the files matching the 3 letter extension that is ext in path, recursively
    # store them in append them to list
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
                os.unlink(hdrfn)
        if not HeaderInfo.exists(n, a):
            # if its not in the HeaderInfo nevral anymore just kill it
            log(5, 'Deleting Header %s' % hdrfn)
            os.unlink(hdrfn)
            

def printtime():
    import time
    return time.strftime('%m/%d/%y %H:%M:%S ', time.localtime(time.time()))

def get_package_info_from_servers(conf, HeaderInfo):
    # this function should be split into - server paths etc and getting the header info/populating the 
    # the HeaderInfo nevral class so we can do non-root runs of yum
    log(2, 'Gathering package information from servers')
    # sorting the servers so that sort() will order them consistently
    serverlist = conf.servers
    serverlist.sort()
    for serverid in serverlist:
        baseurl = conf.serverurl[serverid]
        servername = conf.servername[serverid]
        serverheader = os.path.join(baseurl, 'headers/header.info')
        servercache = conf.servercache[serverid]
        log(6,'server name/cachedir:' + servername + '-' + servercache)
        log(2,'Getting headers from: %s' % (servername))
        localpkgs = conf.serverpkgdir[serverid]
        localhdrs = conf.serverhdrdir[serverid]
        localheaderinfo = os.path.join(servercache, 'header.info')
        if not os.path.exists(servercache):
            os.mkdir(servercache)
        if not os.path.exists(localpkgs):
            os.mkdir(localpkgs)
        if not os.path.exists(localhdrs):
            os.mkdir(localhdrs)
        if not conf.cache:
            log(3, 'getting header.info from server')
            headerinfofn = urlgrab(serverheader, localheaderinfo, copy_local=1)
        else:
            log(3, 'using cached header.info file')
            headerinfofn=localheaderinfo
        log(4,'headerinfofn: ' + headerinfofn)
        HeaderInfoNevralLoad(headerinfofn, HeaderInfo, serverid)

def checkheader(headerfile, name, arch):
    #return true(1) if the header is good
    #return  false(0) if the header is bad
    # test is fairly rudimentary - read in header - read two portions of the header
    h = readHeader(headerfile)
    if h == None:
        return 0
    else:
        if name != h[rpm.RPMTAG_NAME] or arch != h[rpm.RPMTAG_ARCH]:
            return 0
    return 1

def download_headers(HeaderInfo, nulist):
    for (name, arch) in nulist:
        # if header exists - it gets checked
        # if header does not exist it gets downloaded then checked
        # this should happen in a loop - up to 3 times
        # if we can't get a good header after 3 tries we bail.
        checkpass = 1
        LocalHeaderFile = HeaderInfo.localHdrPath(name, arch)
        RemoteHeaderFile = HeaderInfo.remoteHdrUrl(name, arch)
        while checkpass <= 3:
            if os.path.exists(LocalHeaderFile):
                log(5, 'cached %s' % LocalHeaderFile)
            else:
                log(2, 'getting %s' % LocalHeaderFile)
                hdrfn = urlgrab(RemoteHeaderFile, LocalHeaderFile, copy_local=1)
                HeaderInfo.setlocalhdrpath(name, arch, hdrfn)
            if checkheader(LocalHeaderFile, name, arch):
                    break
            else:
                log(3, 'damaged header %s try - %d' % (LocalHeaderFile, checkpass))
                checkpass = checkpass + 1
                os.unlink(LocalHeaderFile)
                good = 0
    if keepalive_handler: keepalive_handler.close_all()
                
def take_action(cmds, nulist, uplist, newlist, obslist, tsInfo, HeaderInfo, rpmDBInfo, obsdict):
    from yummain import usage
    if conf.uid != 0:
        if cmds[0] in ['install','update','clean','upgrade','erase']:
            errorlog(0, _('You need to be root to perform these commands'))
            sys.exit(1)
    if cmds[0] == 'install':
        cmds.remove(cmds[0])
        if len(cmds) == 0:
            errorlog(0, _('Need to pass a list of pkgs to install'))
            usage()
        else:
            pkgaction.installpkgs(tsInfo, nulist, cmds, HeaderInfo, rpmDBInfo)
    elif cmds[0] == 'provides':
        cmds.remove(cmds[0])
        if len(cmds) == 0:
            errorlog(0, _('Need a provides to match'))
            usage()
        else:
            log(2, _('Looking in available packages for a providing package'))
            pkgaction.whatprovides(cmds, nulist, HeaderInfo,0)
            log(2, _('Looking in installed packages for a providing package'))
            pkgaction.whatprovides(cmds, nulist, rpmDBInfo,1)
        sys.exit(0)
    elif cmds[0] == 'update':
        cmds.remove(cmds[0])
        if len(cmds) == 0:
            pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, obslist, 'all')
        else:
            pkgaction.updatepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, obslist, cmds)
    elif cmds[0] == 'upgrade':
        cmds.remove(cmds[0])
        if len(cmds) == 0:
            pkgaction.upgradepkgs(tsInfo, HeaderInfo, rpmDBInfo, nulist, uplist, obslist, obsdict, 'all')
    elif cmds[0] == 'erase' or cmds[0] == 'remove':
        cmds.remove(cmds[0])
        if len(cmds) == 0:
            errorlog (0, _('Need to pass a list of pkgs to erase'))
            usage()
        else:
            pkgaction.erasepkgs(tsInfo, rpmDBInfo, cmds)
    elif cmds[0] == 'list':
        cmds.remove(cmds[0])
        if len(cmds) == 0:
            pkgaction.listpkgs(nulist, 'all', HeaderInfo)
            sys.exit(0)
        else:
            if cmds[0] == 'updates':
                pkgaction.listpkgs(uplist, 'updates', HeaderInfo)
            elif cmds[0] == 'available':
                pkgaction.listpkgs(newlist, 'all', HeaderInfo)
            elif cmds[0] == 'installed':
                pkglist = rpmDBInfo.NAkeys()
                pkgaction.listpkgs(pkglist, 'all', rpmDBInfo)
            elif cmds[0] == 'extras':
                pkglist=[]
                for (name, arch) in rpmDBInfo.NAkeys():
                    if not HeaderInfo.exists(name, arch):
                        pkglist.append((name,arch))
                if len(pkglist) > 0:
                    pkgaction.listpkgs(pkglist, 'all', rpmDBInfo)
                else:
                    log(2, _('No Packages installed not included in a repository'))
            else:    
                log(2, _('Looking in Available Packages:'))
                pkgaction.listpkgs(nulist, cmds, HeaderInfo)
                log(2, _('Looking in Installed Packages:'))
                pkglist = rpmDBInfo.NAkeys()
                pkgaction.listpkgs(pkglist, cmds, rpmDBInfo)
        sys.exit(0)
    elif cmds[0] == 'info':
        cmds.remove(cmds[0])
        if len(cmds) == 0:
            pkgaction.listpkginfo(nulist, 'all', HeaderInfo)
            sys.exit(0)
        else:
            if cmds[0] == 'updates':
                pkgaction.listpkginfo(uplist, 'updates', HeaderInfo)
            elif cmds[0] == 'available':
                pkgaction.listpkginfo(newlist, 'all', HeaderInfo)
            elif cmds[0] == 'installed':
                pkglist=rpmDBInfo.NAkeys()
                pkgaction.listpkginfo(pkglist,'all', rpmDBInfo)
            elif cmds[0] == 'extras':
                pkglist=[]
                for (name, arch) in rpmDBInfo.NAkeys():
                    if not HeaderInfo.exists(name, arch):
                        pkglist.append((name,arch))
                if len(pkglist) > 0:
                    pkgaction.listpkginfo(pkglist, 'all', rpmDBInfo)
                else:
                    log(2, _('No Packages installed not included in a repository'))
            else:    
                log(2, _('Looking in Available Packages:'))
                pkgaction.listpkginfo(nulist, cmds, HeaderInfo)
                log(2, _('Looking in Installed Packages:'))
                pkglist=rpmDBInfo.NAkeys()
                pkgaction.listpkginfo(pkglist, cmds, rpmDBInfo)
        sys.exit(0)

    elif cmds[0] == 'clean':
        cmds.remove(cmds[0])
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
    # FIXME plug sigchecking back in here both md5 and gpg
    # make this work so we don't end up sigchecking twice
    tsfin = rpm.TransactionSet('/')
    for (name, arch) in tsInfo.NAkeys():
        pkghdr = tsInfo.getHeader(name, arch)
        rpmloc = tsInfo.localRpmPath(name, arch)
        serverid = tsInfo.serverid(name, arch)
        state = tsInfo.state(name, arch)
        if state in ('u', 'ud', 'iu', 'i'): # inst/update
            if os.path.exists(rpmloc):
                log(4, 'Using cached %s' % (os.path.basename(rpmloc)))
            else:
                log(2, 'Getting %s' % (os.path.basename(rpmloc)))
                localrpmpath = urlgrab(tsInfo.remoteRpmUrl(name, arch), rpmloc, copy_local=0)
                tsInfo.setlocalrpmpath(name, arch, localrpmpath)
            # we now actually have the rpm and we know where it is - so use it
            rpmloc = tsInfo.localRpmPath(name, arch)
            pkgaction.checkRpmMD5(rpmloc)
            if conf.servergpgcheck[serverid]:
                pkgaction.checkRpmSig(rpmloc, serverid)
            if state == 'i':
                tsfin.addInstall(pkghdr, (pkghdr, rpmloc), 'i')
            else:
                tsfin.addInstall(pkghdr, (pkghdr, rpmloc), 'u')
        elif state == 'a':
            pass
        elif state == 'e' or state == 'ed':
            tsfin.addErase(name)
    if keepalive_handler: keepalive_handler.close_all()
    return tsfin


def checkGPGInstallation():
    if not os.access("/usr/bin/gpg", os.X_OK):
        errorlog(0, _("Error: GPG is not installed"))
        return 1
    return 0
    
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
        
