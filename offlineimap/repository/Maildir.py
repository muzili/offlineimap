# Maildir repository support
# Copyright (C) 2002 John Goerzen
# <jgoerzen@complete.org>
#
#    This program is free software; you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation; either version 2 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program; if not, write to the Free Software
#    Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA

from offlineimap import folder
from offlineimap.ui import getglobalui
from offlineimap.error import OfflineImapError
from offlineimap.repository.Base import BaseRepository
import os
from stat import *

class MaildirRepository(BaseRepository):
    def __init__(self, reposname, account):
        """Initialize a MaildirRepository object.  Takes a path name
        to the directory holding all the Maildir directories."""
        BaseRepository.__init__(self, reposname, account)

        self.root = self.getlocalroot()
        self.folders = None
        self.ui = getglobalui()
        self.debug("MaildirRepository initialized, sep is " + repr(self.getsep()))
        self.folder_atimes = []
        self.deletecounter = 0

        # Create the top-level folder if it doesn't exist
        if not os.path.isdir(self.root):
            os.mkdir(self.root, 0o700)

    def _append_folder_atimes(self, foldername):
        """Store the atimes of a folder's new|cur in self.folder_atimes"""
        p = os.path.join(self.root, foldername)
        new = os.path.join(p, 'new')
        cur = os.path.join(p, 'cur')
        atimes = (p, os.path.getatime(new), os.path.getatime(cur))
        self.folder_atimes.append(atimes)

    def restore_atime(self):
        """Sets folders' atime back to their values after a sync

        Controlled by the 'restoreatime' config parameter."""
        if not self.getconfboolean('restoreatime', False):
            return # not configured to restore

        for (dirpath, new_atime, cur_atime) in self.folder_atimes:
            new_dir = os.path.join(dirpath, 'new')
            cur_dir = os.path.join(dirpath, 'cur')
            os.utime(new_dir, (new_atime, os.path.getmtime(new_dir)))
            os.utime(cur_dir, (cur_atime, os.path.getmtime(cur_dir)))

    def getlocalroot(self):
        return os.path.expanduser(self.getconf('localfolders'))

    def debug(self, msg):
        self.ui.debug('maildir', msg)

    def getsep(self):
        return self.getconf('sep', '.').strip()

    def makefolder(self, foldername):
        """Create new Maildir folder if necessary

        This will not update the list cached in getfolders(). You will
        need to invoke :meth:`forgetfolders` to force new caching when
        you are done creating folders yourself.

        :param foldername: A relative mailbox name. The maildir will be
            created in self.root+'/'+foldername. All intermediate folder
            levels will be created if they do not exist yet. 'cur',
            'tmp', and 'new' subfolders will be created in the maildir.
        """
        self.ui.makefolder(self, foldername)
        if self.account.dryrun:
            return
        full_path = os.path.abspath(os.path.join(self.root, foldername))
    
        # sanity tests
        if self.getsep() == '/':
            for component in foldername.split('/'):
                assert not component in ['new', 'cur', 'tmp'],\
                    "When using nested folders (/ as a Maildir separator), "\
                    "folder names may not contain 'new', 'cur', 'tmp'."
        assert foldername.find('../') == -1, "Folder names may not contain ../"
        assert not foldername.startswith('/'), "Folder names may not begin with /"

        # If we're using hierarchical folders, it's possible that
        # sub-folders may be created before higher-up ones.
        self.debug("makefolder: calling makedirs '%s'" % full_path)
        try:
            os.makedirs(full_path, 0o700)
        except OSError as e:
            if e.errno == 17 and os.path.isdir(full_path):
                self.debug("makefolder: '%s' already a directory" % foldername)
            else:
                raise
        for subdir in ['cur', 'new', 'tmp']:
            try:
                os.mkdir(os.path.join(full_path, subdir), 0o700)
            except OSError as e:
                if e.errno == 17 and os.path.isdir(full_path):
                    self.debug("makefolder: '%s' already has subdir %s" %
                               (foldername, subdir))
                else:
                    raise

    def deletefolder(self, foldername):
        self.ui.warn("NOT YET IMPLEMENTED: DELETE FOLDER %s" % foldername)

    def getfolder(self, foldername):
        """Return a Folder instance of this Maildir

        If necessary, scan and cache all foldernames to make sure that
        we only return existing folders and that 2 calls with the same
        name will return the same object."""
        # getfolders() will scan and cache the values *if* necessary
        folders = self.getfolders()
        for folder in folders:
            if foldername == folder.name:
                return folder
        raise OfflineImapError("getfolder() asked for a nonexisting "
                               "folder '%s'." % foldername,
                               OfflineImapError.ERROR.FOLDER)

    def _getfolders_scandir(self, root, extension = None):
        """Recursively scan folder 'root'; return a list of MailDirFolder

        :param root: (absolute) path to Maildir root
        :param extension: (relative) subfolder to examine within root"""
        self.debug("_GETFOLDERS_SCANDIR STARTING. root = %s, extension = %s" \
                   % (root, extension))
        retval = []

        # Configure the full path to this repository -- "toppath"
        if extension:
            toppath = os.path.join(root, extension)
        else:
            toppath = root
        self.debug("  toppath = %s" % toppath)

        # Iterate over directories in top & top itself.
        for dirname in os.listdir(toppath) + ['']:
            self.debug("  dirname = %s" % dirname)
            if dirname == '' and extension is not None:
                self.debug('  skip this entry (already scanned)')
                continue
            if dirname in ['cur', 'new', 'tmp']:
                self.debug("  skip this entry (Maildir special)")
                # Bypass special files.
                continue
            fullname = os.path.join(toppath, dirname)
            if not os.path.isdir(fullname):
                self.debug("  skip this entry (not a directory)")
                # Not a directory -- not a folder.
                continue
            if extension:
                # extension can be None which fails.
                foldername = os.path.join(extension, dirname)
            else:
                foldername = dirname

            if (os.path.isdir(os.path.join(fullname, 'cur')) and
                os.path.isdir(os.path.join(fullname, 'new')) and
                os.path.isdir(os.path.join(fullname, 'tmp'))):
                # This directory has maildir stuff -- process
                self.debug("  This is maildir folder '%s'." % foldername)
                if self.getconfboolean('restoreatime', False):
                    self._append_folder_atimes(foldername)
                retval.append(folder.Maildir.MaildirFolder(self.root,
                                                           foldername,
                                                           self.getsep(),
                                                           self))

            if self.getsep() == '/' and dirname != '':
                # Recursively check sub-directories for folders too.
                retval.extend(self._getfolders_scandir(root, foldername))
        self.debug("_GETFOLDERS_SCANDIR RETURNING %s" % \
                   repr([x.getname() for x in retval]))
        return retval

    def getfolders(self):
        if self.folders == None:
            self.folders = self._getfolders_scandir(self.root)
        return self.folders

    def forgetfolders(self):
        """Forgets the cached list of folders, if any.  Useful to run
        after a sync run."""
        self.folders = None

    def _getmoves(self):
        for folder in self.getfolders():
            try:
                for entry in os.listdir(os.path.join(folder.getfullname(), "mv")):
                    fn = os.path.join(folder.getfullname(), "mv", entry)
                    newdest = open(fn).read().strip()
                    newfolder = os.path.dirname(os.path.dirname(os.path.relpath(newdest, self.root)))
                    yield fn,\
                          folder.getname(),\
                          newfolder,\
                          entry,\
                          os.path.join(os.path.basename(os.path.dirname(newdest)), os.path.basename(newdest)),\
                          newdest
            except OSError:
                continue

    def _getmoves_postdelete(self):
        for x in self._getmoves():
            try:
                yield x
            finally:
                # Remove entries after processing, since it's harmless
                # to double-process except from a performance
                # perspective
                os.unlink(x[0])
    
    def syncmoves(self, remoterepos, statusrepos):
        # TODO thread me!
        # TODO does not respect do not sync (this is actually kind of useful)

        if not next(self._getmoves(), False):
            return

        # Initialize by instantiating cache for all status folders
        # We setup cache for access because we need messagelist to
        # persist over invocations to 'getfolder' (why doesn't
        # caching give us that already?)
        cache = {}
        for statusfolder in statusrepos.getfolders():
            statusfolder.cachemessagelist()
            cache[statusfolder.getname()] = statusfolder
        dirty_status = set() # relies on object identity

        # XXX make config option
        save_interval = 100

        for fn, oldfolder, newfolder, old_filename, filename, fullname in self._getmoves_postdelete():
            print fn

            # example data:
            #
            # fn = /home/ezyang/Mail/MIT/INBOX/mv/1345706203_9.22226.javelin,U=400917,FMD5=7e33429f656f1e6e9d79b29c3f82c57e:2,
            # oldfolder = INBOX
            # newfolder = INBOX.Archive
            # old_filename = 1345706203_9.22226.javelin,U=400917,FMD5=7e33429f656f1e6e9d79b29c3f82c57e:2,
            # filename = new/1345706203_9.22226.javelin,U=400917,FMD5=7e33429f656f1e6e9d79b29c3f82c57e:2,S
            # fullname = /home/ezyang/Mail/MIT/INBOX.Archive/new/1345706203_9.22226.javelin,U=400917,FMD5=7e33429f656f1e6e9d79b29c3f82c57e:2,S
            #
            # old_filename doesn't have leading cur/ or new/, we only
            # care about it for the old flags value (XXX the value is
            # inaccurate if Sup first changed the flag (move 1) and then
            # moved source (move 2); we do OK if the change is atomic
            # e.g. you did 'A')

            if oldfolder == newfolder:
                continue

            if not os.path.exists(fullname):
                continue

            # XXX uid validity check (twice)
            # XXX readonly
            # XXX error handling and UI
            # XXX restoreatime

            # If we fail, we bail out and just ask the later phases
            # to do it the slow way.

            # Note: newfolder/filename == access in Maildir

            # - Lookup old and new folders both local and IMAP
            local_oldfolder = self.getfolder(oldfolder) # Must be Maildir
            local_newfolder = self.getfolder(newfolder) # ditto
            remote_oldfolder = remoterepos.getfolder(oldfolder.replace(self.getsep(), remoterepos.getsep()))
            remote_newfolder = remoterepos.getfolder(newfolder.replace(self.getsep(), remoterepos.getsep()))
            status_oldfolder = cache[oldfolder.replace(self.getsep(), statusrepos.getsep())]
            status_newfolder = cache[newfolder.replace(self.getsep(), statusrepos.getsep())]
            # We don't read out the message lists for local/remote, since the
            # filenames *give us the information we need*.

            # - Parse filename into uid and flags AND
            #   (Note: Use old folder so that the directory is correct)
            # XXX I think old_flags is strictly unnecessary
            _, _, _, old_flags = local_oldfolder._parse_filename(old_filename) # data will get overwritten
            _, uid, _, flags = local_oldfolder._parse_filename(filename)

            if uid < 0:
                continue

            try:
                newuid = remote_oldfolder.remotecopymessage(uid, remote_newfolder)
            except NotImplementedError:
                continue
            if newuid <= 0:
                continue

            # Don't expunge all the time, since it is pretty slow. But
            # don't delay too long, because you'll run out of disk space that
            # way.
            self.deletecounter += 1
            if self.deletecounter % save_interval != 0:
                remote_oldfolder.expunge = False
            remote_oldfolder.deletemessage(uid)

            # Rename the file to the newuid, so that we don't have to go
            # through another pass of the can
            local_newfolder._move_file(filename, newuid, flags)

            # We're not using methods because they are too slow (and it is
            # safe not to update status)
            status_newfolder.messagelist[newuid] = {'uid': newuid, 'flags': old_flags }
            if uid in status_oldfolder.messagelist:
                del status_oldfolder.messagelist[uid]
            dirty_status.add(status_newfolder)
            dirty_status.add(status_oldfolder)
            if self.deletecounter % save_interval == 0:
                for status in dirty_status:
                    status.save()
                dirty_status.clear()

            print "Moved %s" % newuid

        for remotefolder in remoterepos.getfolders():
            remotefolder.doexpunge()
        for statusfolder in cache.values():
            statusfolder.save()

        # This is critical, since we've polluted the message cache
        # (especially for self and remote; probably status
        # can get away with not doing this)
        self.forgetfolders()
        remoterepos.forgetfolders()
        statusrepos.forgetfolders()
