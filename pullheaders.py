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

#NTS XXX might be worthwhile to have an option to do sigchecking on the server side


import os
import sys
import rpm
import serverStuff
from logger import Logger

log=Logger(threshold=0,default=2,prefix='',preprefix='')
serverStuff.log = log

def genhdrs(rpms,headerdir,rpmcheck,compress):
    rpmdelete = 0 # define this if you have the rpmheader stripping patch built into rpm
    rpminfo = {}
    numrpms = len(rpms)
    goodrpm = 0
    currpm = 0
    for rpmfn in rpms:
        rpmname = os.path.basename(rpmfn)
        currpm=currpm + 1
        percent = (currpm*100)/numrpms
        sys.stdout.write('\r' + ' ' * 80)
        sys.stdout.write("\rDigesting rpms %d %% complete: %s" % (percent,rpmname))
        sys.stdout.flush()
        if rpmcheck==1:
            log(2,"\nChecking sig on %s" % (rpmname))
            serverStuff.checkSig(rpmfn)
        header=serverStuff.readHeader(rpmfn)
        #check to ignore src.rpms
        if header != 'source':
            if header[rpm.RPMTAG_EPOCH] == None:
                epoch = '0'
            else:
                epoch = '%s' % header[rpm.RPMTAG_EPOCH]
            name = header[rpm.RPMTAG_NAME]
            ver = header[rpm.RPMTAG_VERSION]
            rel = header[rpm.RPMTAG_RELEASE]
            arch = header[rpm.RPMTAG_ARCH]
            rpmloc = rpmfn
            rpmtup = (name,arch)
            # do we already have this name.arch tuple in the dict?
            if rpminfo.has_key(rpmtup):
                log(2,"Already found tuple: %s %s " % (name, arch))
                (e1, v1, r1, l1) = rpminfo[rpmtup]
                oldhdrfile = "%s/%s-%s-%s-%s.%s.hdr" % (headerdir, name, e1, v1, r1, arch) 
                # which one is newer?
                rc = rpm.labelCompare((e1,v1,r1), (epoch, ver, rel))
                if rc <= -1:
                    # if the more recent one in is newer then throw away the old one
                    del rpminfo[rpmtup]
                    if os.path.exists(oldhdrfile):
                        print "\nignoring older pkg: %s" % (l1)
                        os.unlink(oldhdrfile)
                    if rpmdelete:
                        shortheader = serverStuff.cleanHeader(header)
                    else:
                        shortheader = header
                    headerloc = serverStuff.writeHeader(headerdir,shortheader,compress)       
                    rpminfo[rpmtup]=(epoch,ver,rel,rpmloc)
                elif rc == 0:
                    # hmm, they match complete - warn the user that they've got a dupe in the tree
                    print "\nignoring dupe pkg: %s" % (rpmloc)
                elif rc >= 1:
                    # move along, move along, nothing more to see here
                    print "\nignoring older pkg: %s" % (rpmloc)
            else:
                if rpmdelete:
                    shortheader = serverStuff.cleanHeader(header)
                else:
                    shortheader = header
                headerloc = serverStuff.writeHeader(headerdir,shortheader,compress)
                rpminfo[rpmtup]=(epoch,ver,rel,rpmloc)
                goodrpm = goodrpm + 1
        else:
            log(2,"ignoring srpm: %s" % rpmfn)
   
    print "\n   Total: %d\n   Used: %d" %(numrpms, goodrpm)
    return rpminfo
    
def main():
    headerdir = 'headers'
    headerinfo = headerdir + '/' + 'header.info'
    checkdeps=0
    writehdrs=1
    rpmcheck=0
    compress=1
    usesymlinks=0
    if  len(sys.argv) < 2:
        serverStuff.Usage()
    args = sys.argv[1:]
    basedir = args[-1]
    del args[-1]
    for arg in args:
        if arg == "-v":
            log.verbosity=4
        if arg == "-d":
            checkdeps=1
        if arg == "-n":
            writehdrs=0
        if arg == "-c":
            rpmcheck=1
        if arg == "-z":
            compress=1
        if arg == "-l":
            usesymlinks=1
        if arg in ['-h','--help']:
            serverStuff.Usage()
    #save where we are right now
    curdir = os.getcwd()
    #start the sanity/stupidity checks
    if not os.path.exists(basedir):
        print "Directory of rpms must exist"
        serverStuff.Usage()
    if not os.path.isdir(basedir):
        print "Directory of rpms must be a directory."
        sys.exit(1)
        
    #change to the basedir to work from w/i the path - for relative url paths
    os.chdir(basedir)

    #get the list of rpms
    rpms=serverStuff.getfilelist('./', '.rpm', [], usesymlinks)
    #and a few more sanity checks
    if len(rpms) < 1:
        print "No rpms to look at. Exiting."
        sys.exit(1)

    if checkdeps==1:
        (error,msgs) = serverStuff.depchecktree(rpms)
        if error==1:
            print "Errors within the dir(s):\n %s" % basedir
            for msg in msgs:
                print "   " + msg
            sys.exit(1)
        else:
            print "All dependencies resolved and no conflicts detected"
    
    if writehdrs==1:
        #if the headerdir exists and its a file then we're in deep crap
        if os.path.isfile(headerdir):
            print "%s is a file" % (headerdir)
            sys.exit(1)

        #if it doesn't exist then make the dir
        if not os.path.exists(headerdir):
            os.mkdir(headerdir)
        # done with the sanity checks, on to the cleanups
        #looks for a list of .hdr files and the header.info file
        hdrlist = serverStuff.getfilelist(headerdir, '.hdr', [], 0)

        #removes both entirely 
        for hdr in hdrlist:
            os.unlink(hdr)
        if os.path.exists(headerinfo):
            os.unlink(headerinfo)
        rpminfo = genhdrs(rpms, headerdir,rpmcheck,compress)

        #Write header.info file
        print "\nWriting header.info file"
        headerfd = open(headerinfo, "w")
        for item in rpminfo.keys():
            (name,arch) = item
            (epoch, ver, rel, rpmloc) = rpminfo[item]
            info = "%s:%s-%s-%s.%s=%s\n" % (epoch, name, ver, rel, arch, rpmloc)
            headerfd.write(info)
        headerfd.close()


    #take us home mr. data
    os.chdir(curdir)


if __name__ == "__main__":
    main()


