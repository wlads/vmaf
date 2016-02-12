import multiprocessing
import os
import subprocess
from tools import make_parent_dirs_if_nonexist, get_dir_without_last_slash

__copyright__ = "Copyright 2016, Netflix, Inc."
__license__ = "Apache, Version 2.0"

from mixin import TypeVersionEnabled
import config

class Executor(TypeVersionEnabled):
    """
    Executor takes in a list of assets, and run calculation on them, and return
    a list of corresponding results. An Executor must specify a unique type
    and version combination (by the TYPE and VERSION attribute), so that the
    Result generated by it can be identified.
    """

    def __init__(self,
                 assets,
                 logger,
                 log_file_dir= config.ROOT + "/workspace/log_file_dir",
                 fifo_mode=True,
                 delete_workdir=True):

        TypeVersionEnabled.__init__(self)

        self.assets = assets
        self.logger = logger
        self.log_file_dir = log_file_dir
        self.fifo_mode = fifo_mode
        self.delete_workdir = delete_workdir
        self.results = []

        self._assert_assets()

    @property
    def executor_id(self):
        return TypeVersionEnabled.get_type_version_string(self)

    def run(self):

        if self.logger:
            self.logger.info(
                "For each asset, if {type} log has not been generated, "
                "run and generate {type} log file...".format(type=self.TYPE))

        # run generate_log_file on each asset
        map(self._run_and_generate_log_file_wrapper, self.assets)

        if self.logger:
            self.logger.info("Read {type} log file, get quality scores...".
                             format(type=self.TYPE))

        # collect result from each asset's log file
        results = map(self._read_result, self.assets)

        self.results = results

    def remove_logs(self):
        for asset in self.assets:
            self._remove_log(asset)

    def _assert_assets(self):

        list_dataset_contentid_assetid = \
            map(lambda asset: (asset.dataset, asset.content_id, asset.asset_id),
                self.assets)
        assert len(list_dataset_contentid_assetid) == \
               len(set(list_dataset_contentid_assetid)), \
            "Triplet of dataset, content_id and asset_id must be unique for each asset."

    @staticmethod
    def _assert_an_asset(asset):

        # 1) for now, quality width/height has to agree with ref/dis width/height
        assert asset.quality_width_height \
               == asset.ref_width_height \
               == asset.dis_width_height
        # 2) ...
        # 3) ...

    def _run_and_generate_log_file(self, asset):

        log_file_path = self._get_log_file_path(asset)

        # if parent dir doesn't exist, create
        make_parent_dirs_if_nonexist(log_file_path)

        # touch (to start with a clean co)
        with open(log_file_path, 'wt'):
            pass

        # add runner type and version
        with open(log_file_path, 'at') as log_file:
            log_file.write("{type} VERSION {version}\n\n".format(
                type=self.TYPE, version=self.VERSION))

    def _run_and_generate_log_file_wrapper(self, asset):
        """
        Wraper around the essential function _run_and_generate_log_file, to
        do housekeeping work including 1) asserts of asset, 2) skip run if
        log already exist, 3) creating fifo, 4) delete work file and dir
        :param asset:
        :return:
        """

        # asserts
        self._assert_an_asset(asset)

        log_file_path = self._get_log_file_path(asset)

        if os.path.isfile(log_file_path):
            if self.logger:
                self.logger.info(
                    '{type} log {log_file_path} exists. Skip {type} '
                    'run.'.format(type=self.TYPE,
                                  log_file_path=log_file_path))
        else:
            # remove workfiles if exist (do early here to avoid race condition
            # when ref path and dis path have some overlap)
            self._close_ref_workfile(asset)
            self._close_dis_workfile(asset)

            make_parent_dirs_if_nonexist(asset.ref_workfile_path)
            make_parent_dirs_if_nonexist(asset.dis_workfile_path)

            if self.fifo_mode:
                ref_p = multiprocessing.Process(target=self._open_ref_workfile,
                                                args=(asset, True))
                dis_p = multiprocessing.Process(target=self._open_dis_workfile,
                                                args=(asset, True))
                ref_p.start()
                dis_p.start()
            else:
                self._open_ref_workfile(asset, fifo_mode=False)
                self._open_dis_workfile(asset, fifo_mode=False)

            self._run_and_generate_log_file(asset)

            if self.delete_workdir:
                self._close_ref_workfile(asset)
                self._close_dis_workfile(asset)

                ref_dir = get_dir_without_last_slash(asset.ref_workfile_path)
                dis_dir = get_dir_without_last_slash(asset.dis_workfile_path)
                os.rmdir(ref_dir)
                try:
                    os.rmdir(dis_dir)
                except OSError as e:
                    if e.errno == 2: # [Errno 2] No such file or directory
                        # already removed by os.rmdir(ref_dir)
                        pass

    def _get_log_file_path(self, asset):
        return "{dir}/{type}/{str}".format(dir=self.log_file_dir,
                                           type=self.TYPE, str=str(asset))

    # ===== workfile =====

    def _open_ref_workfile(self, asset, fifo_mode):
        """
        For now, only works for YUV format -- all need is to copy from ref file
        to ref workfile
        :param asset:
        :param fifo_mode:
        :return:
        """
        src = asset.ref_path
        dst = asset.ref_workfile_path

        # if fifo mode, mkfifo
        if fifo_mode:
            os.mkfifo(dst)

        # open ref file
        self._open_file(src, dst)

    def _open_dis_workfile(self, asset, fifo_mode):
        """
        For now, only works for YUV format -- all need is to copy from dis file
        to dis workfile
        :param asset:
        :param fifo_mode:
        :return:
        """
        src = asset.dis_path
        dst = asset.dis_workfile_path

        # if fifo mode, mkfifo
        if fifo_mode:
            os.mkfifo(dst)

        # open dis file
        self._open_file(src, dst)

    def _open_file(self, src, dst):
        """
        For now, only works if source is YUV -- all needed is to copy
        :param src:
        :param dst:
        :return:
        """
        # NOTE: & is required for fifo mode !!!!
        cp_cmd = "cp {src} {dst} &". \
            format(src=src, dst=dst)
        if self.logger:
            self.logger.info(cp_cmd)
        subprocess.call(cp_cmd, shell=True)

    @staticmethod
    def _close_ref_workfile(asset):
        path = asset.ref_workfile_path
        if os.path.exists(path):
            os.remove(path)

    @staticmethod
    def _close_dis_workfile(asset):
        path = asset.dis_workfile_path
        if os.path.exists(path):
            os.remove(path)

