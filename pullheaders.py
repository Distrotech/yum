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


import os
import sys
import rpmUtils
import serverStuff
import rpm
from logger import Logger
from i18n import _

log=Logger(threshold=0, default=2, prefix='', preprefix='')
serverStuff.log = log
rpmUtils.log = log
rpmUtils.errorlog = log
ts = rpm.TransactionSet()
rpmUtils.ts = ts
serverStuff.ts = ts

def main():
    headerdir = 'headers'
    headerinfo = headerdir + '/' + 'header.info'
    if  len(sys.argv) < 2:
        serverStuff.Usage()
    cmds = {}
    cmds['checkdeps'] = 0
    cmds['writehdrs'] = 1
    cmds['rpmcheck'] = 0
    cmds['compress'] = 1
    cmds['usesymlinks'] = 0
    cmds['quiet'] = 0
    cmds['loud'] = 0
    args = sys.argv[1:]
    basedir = args[-1]
    del args[-1]
    for arg in args:
        if arg == "-v":
            cmds['loud'] = 1
        elif arg == "-d":
            cmds['checkdeps'] = 1
        elif arg == "-n":
            cmds['writehdrs'] = 0
        elif arg == "-c":
            cmds['rpmcheck'] = 1
        elif arg == "-z":
            cmds['compress'] = 1
        elif arg == "-l":
            cmds['usesymlinks'] = 1
        elif arg == "-q":
            cmds['quiet'] = 1
        elif arg == "-vv":
            cmds['loud'] = 1
            log.verbosity = 4
        if arg in ['-h','--help']:
            serverStuff.Usage()
    # save where we are right now
    curdir = os.getcwd()
    # start the sanity/stupidity checks
    if not os.path.exists(basedir):
        print _("Directory of rpms must exist")
        serverStuff.Usage()
    if not os.path.isdir(basedir):
        print _("Directory of rpms must be a directory.")
        sys.exit(1)
        
    # change to the basedir to work from w/i the path - for relative url paths
    os.chdir(basedir)
    
    # get the list of rpms
    rpms=serverStuff.getfilelist('./', '.rpm', [], cmds['usesymlinks'])

    # some quick checks - we know we don't have ANY rpms - so, umm what do we
    # do? - if we have a headers dir then maybe we already had some and its
    # a now-empty repo - well, lets clean it up
    # kill the hdrs, kill the header.info - write an empty one
    if len(rpms) == 0:
        if os.path.exists(headerdir):
            hdrlist = serverStuff.getfilelist(headerdir, '.hdr', [], 0)
            removeCurrentHeaders(headerdir, hdrlist)
            removeHeaderInfo(headerinfo)
            headerfd = open(headerinfo, "w")
            headerfd.close()
            sys.exit(0)
        else:
            print _('No rpms to work with and no header dir. Exiting.')
            sys.exit(1)
            
    # depcheck if requested
    if cmds['checkdeps']:
        (error,msgs) = serverStuff.depchecktree(rpms)
        if error==1:
            print _("Errors within the dir(s):\n %s") % basedir
            for msg in msgs:
                print _("   ") + msg
            sys.exit(1)
        else:
            print _("All dependencies resolved and no conflicts detected")
    
    if cmds['writehdrs']:
        # if the headerdir exists and its a file then we're in deep crap
        if os.path.isfile(headerdir):
            print _("%s is a file") % (headerdir)
            sys.exit(1)

        # if it doesn't exist then make the dir
        if not os.path.exists(headerdir):
            os.mkdir(headerdir)
        # done with the sanity checks, on to the cleanups
        
        # looks for a list of .hdr files and the header.info file
        hdrlist = serverStuff.getfilelist(headerdir, '.hdr', [], 0)
        removeCurrentHeaders(headerdir, hdrlist)
        removeHeaderInfo(headerinfo)
        
        # generate the new headers
        rpminfo = genhdrs(rpms, headerdir, cmds)

        # Write header.info file
        print _("\nWriting header.info file")
        headerfd = open(headerinfo, "w")
        for item in rpminfo.keys():
            (name,arch) = item
            (epoch, ver, rel, rpmloc) = rpminfo[item]
            info = "%s:%s-%s-%s.%s=%s\n" % (epoch, name, ver, rel, arch, rpmloc)
            headerfd.write(info)
        headerfd.close()

    # take us home mr. data
    os.chdir(curdir)

def removeCurrentHeaders(headerdir, hdrlist):
    """remove the headers before building the new ones"""
    for hdr in hdrlist:
        if os.path.exists(hdr):
            try:
                os.unlink(hdr)
            except OSerror, e:
                print _('Cannot delete file %s') % hdr
        else:
            print _('Odd header %s suddenly disappeared') % hdr

def removeHeaderInfo(headerinfo):
    """remove header.info file"""
    if os.path.exists(headerinfo):
        try:
            os.unlink(headerinfo)
        except OSerror, e:
            print _('Cannot delete header.info - check perms') % hdr
            

def genhdrs(rpms,headerdir,cmds):
    rpminfo = {}
    numrpms = len(rpms)
    goodrpm = 0
    currpm = 0
    for rpmfn in rpms:
        rpmname = os.path.basename(rpmfn)
        currpm=currpm + 1
        percent = (currpm*100)/numrpms
        if not cmds['quiet']:
            if cmds['loud']:
                print _('Digesting rpm - %s - %d/%d') % (rpmname, currpm, numrpms)
            else:
                sys.stdout.write('\r' + ' ' * 80)
                sys.stdout.write("\rDigesting rpms %d %% complete: %s" % (percent, rpmname))
                sys.stdout.flush()
        if cmds['rpmcheck']:
            log(2,_("\nChecking sig on %s") % rpmname)
            if rpmUtils.checkSig(rpmfn) > 0:
                log(0, _("\n\nProblem with gpg sig or md5sum on %s\n\n") % rpmfn)
                sys.exit(1)
        hobj = rpmUtils.RPM_Work(rpmfn)
        #check to ignore src.rpms
        if hobj.isSource():
            log(2,"ignoring srpm: %s" % rpmfn)
        else:
            (name, epoch, ver, rel, arch) = hobj.nevra()
            if epoch is None:
                epoch = '0'
            rpmloc = rpmfn
            rpmtup = (name, arch)
            # do we already have this name.arch tuple in the dict?
            if rpminfo.has_key(rpmtup):
                log(2, _("Already found tuple: %s %s ") % (name, arch))
                (e1, v1, r1, l1) = rpminfo[rpmtup]
                oldhdrfile = "%s/%s-%s-%s-%s.%s.hdr" % (headerdir, name, e1, v1, r1, arch) 
                # which one is newer?
                rc = rpmUtils.compareEVR((e1,v1,r1), (epoch, ver, rel))
                if rc <= -1:
                    # if the more recent one in is newer then throw away the old one
                    del rpminfo[rpmtup]
                    if os.path.exists(oldhdrfile):
                        print _("\nignoring older pkg: %s") % (l1)
                        os.unlink(oldhdrfile)
                    headerloc = hobj.writeHeader(headerdir, cmds['compress'])
                    rpminfo[rpmtup]=(epoch,ver,rel,rpmloc)
                elif rc == 0:
                    # hmm, they match complete - warn the user that they've got a dupe in the tree
                    print _("\nignoring dupe pkg: %s") % (rpmloc)
                elif rc >= 1:
                    # move along, move along, nothing more to see here
                    print _("\nignoring older pkg: %s") % (rpmloc)
            else:
                headerloc = hobj.writeHeader(headerdir, cmds['compress'])
                rpminfo[rpmtup]=(epoch,ver,rel,rpmloc)
                goodrpm = goodrpm + 1
    if not cmds['quiet']:
        print _("\n   Total: %d\n   Used: %d") %(numrpms, goodrpm)
    return rpminfo



if __name__ == "__main__":
    main()


