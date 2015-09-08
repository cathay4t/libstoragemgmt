# Copyright (C) 2015-2016 Red Hat, Inc.
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; If not, see <http://www.gnu.org/licenses/>.
#
# Author: Gris Ge <fge@redhat.com>

## Variable Naming scheme:
#   ssm_sys         SSM_StorageSystem
#   ssm_pro_info    SSM_ProvenanceInfo
#
#   lsm_sys         Object of LSM System
#   lsm_pool        Object of LSM Pool
#   lsm_vol         Object of LSM Volume

import pywbem
import re

from lsm import (IPlugin, uri_parse, LsmError, ErrorNumber,
                 JobStatus, md5, System, Volume, AccessGroup, Pool,
                 VERSION, TargetPort,
                 search_property)
import lsm

from lsm.plugin.ssm.utils import handle_cim_errors

_IAAN_WBEM_HTTPS_PORT = 5989
_IAAN_WBEM_HTTP_PORT = 5988


def _huawei_embeded_inst_to_dict(embed_inst):
    """
    Huawei does not support EmbeddedInstance yet, they just store things in
    to a string list like:
        [u'Manufacture=Huawei,PartNumber=OceanStor']
    """
    tmp_list = re.split(",|=", embed_inst[0])

    return dict(zip(tmp_list[0::2], tmp_list[1::2]))


def _ssm_sys_to_lsm(ssm_sys):
    sys_id = ssm_sys['InstanceID']
    prov_info = _huawei_embeded_inst_to_dict(ssm_sys['ProvenanceInfo'])

    name = "%s: %s" % (ssm_sys['DurableName'], ssm_sys['GivenName'])
    # TODO: The DurableName is not a good way for name considering it has
    #       many format. Better way is to use vender, model and user given
    #       name from ProvenanceInfo.

    # name = "%s %s %s, FW: %s" % (
    #     ssm_pro_info['Manufacturer'], ssm_pro_info['PartNumber'],
    #    ssm_pro_info['Model'], ssm_pro_info['FirmwareVersionString'])

    # TODO(Gris Ge): Waiting Huawei to fix their implementation.
    status = System.STATUS_UNKNOWN
    # TODO(Gris Ge): Use 'ElementCausingError' property to generate
    #                status_info.
    status_info = ''
    plugin_data = None
    # TODO: Add firmware version string.

    return System(sys_id, name, status, status_info, plugin_data)

def _extract_ssm_space_info(ssm_space_info):
    """
    Return total space and free space in bytes.
    """
    return ssm_space_info['AvailableSpace']


def _pool_status(op_status_list):
    """
    TODO(Gris Ge): Convert SSM_OperationalStatusInfo to Pool.STATUS_XXX and
    status info.
    """
    status_info = ''
    # TODO: We just search OK here, should do all status map work here.
    for op_status in op_status_list:
        if 'OK' in op_status['Status']:
            return Pool.STATUS_OK, ''

        status_info += op_status['Description']

    return Pool.STATUS_UNKNOWN, status_info


def _ssm_pool_to_lsm(ssm_pool):
    element_type = Pool.ELEMENT_TYPE_VOLUME
    # ^ TODO(Gris Ge): SSM has no property indicate so.
    unsupported_actions = Pool.UNSUPPORTED_VOLUME_SHRINK
    # ^ TODO(Gris Ge): Hardcoded for huawei.

    space_info = _huawei_embeded_inst_to_dict(ssm_pool['SpaceInfo'])

    free_space = long(space_info.get('AvailableSpace',
                                     Pool.FREE_SPACE_NOT_FOUND))

    total_space = long(space_info.get('ProvisionedSpace',
                                       Pool.TOTAL_SPACE_NOT_FOUND))

    # BUG(huawei): They are using single OperationalStatus in stead of
    #              the list.
    op_status_list = []
    op_status_list.append( _huawei_embeded_inst_to_dict(
        ssm_pool['OperationalStatus']))

    (status, status_info) = _pool_status(op_status_list)

    return Pool(ssm_pool['InstanceID'], ssm_pool['GivenName'],
                element_type, unsupported_actions, total_space, free_space,
                status, status_info, ssm_pool['SystemID'], _plugin_data=None)

def _ssm_vol_to_lsm(ssm_vol):
    # TODO(Gris Ge): Check DurableNameFormat, should be VPD83NAA6 or VPD83NAA5
    vpd83 = ssm_vol['DurableName']
    block_size = int(ssm_vol['BlockSize'])
    space_info = _huawei_embeded_inst_to_dict(ssm_vol['SpaceInfo'])
    num_of_blocks = long(long(space_info['AvailableSpace']) / block_size)
    admin_state = Volume.ADMIN_STATE_ENABLED
    # ^ Should check SSM_StorageVolume['Access']
    pool_id = ssm_vol['PoolID'][0]

    return Volume(ssm_vol['InstanceID'], ssm_vol['GivenName'], vpd83,
                  block_size, num_of_blocks, admin_state, ssm_vol['SystemID'],
                  pool_id, None)

class SSM(IPlugin):
    """
    TODO: Add notes here about general workflow.
    """
    def __init__(self):
        self._c = None
        self.tmo = 0

    def _ssm_pool_of_id(self, pool_id):
        ssm_pools = self._c.EnumerateInstances('SSM_StoragePool')
        for ssm_pool in ssm_pools:
            if ssm_pool['InstanceID'] == pool_id:
                return ssm_pool
        raise LsmError(ErrorNumber.NOT_FOUND_POOL,
                       "Pool not found")

    def _ssm_sys_of_id(self, sys_id):
        ssm_syss = self._c.EnumerateInstances('SSM_StorageSystem')
        for ssm_sys in ssm_syss:
            if ssm_sys['InstanceID'] == sys_id:
                return ssm_sys
        raise LsmError(ErrorNumber.NOT_FOUND_SYSTEM,
                       "System not found")

    def _ssm_vol_of_id(self, vol_id):
        ssm_vols = self._c.EnumerateInstances('SSM_StorageVolume')
        for ssm_vol in ssm_vols:
            if ssm_vol['InstanceID'] == vol_id:
                return ssm_vol
        raise LsmError(ErrorNumber.NOT_FOUND_VOLUME,
                       "Volume not found")

    @handle_cim_errors
    def plugin_register(self, uri, password, timeout,
                        flags=lsm.Client.FLAG_RSVD):
        """
        Called when the plug-in runner gets the start request from the client.
        """
        protocol = 'http'
        port = _IAAN_WBEM_HTTP_PORT
        u = uri_parse(uri, ['scheme', 'netloc', 'host'], None)

        if u['scheme'].lower() == 'ssm+ssl':
            protocol = 'https'
            port = _IAAN_WBEM_HTTPS_PORT

        if 'port' in u:
            port = u['port']

        url = "%s://%s:%s" % (protocol, u['host'], port)

        namespace = None
        if 'namespace' in u['parameters']:
            namespace = u['parameters']['namespace']

        if namespace is None:
            raise LsmError(
                ErrorNumber.INVALID_ARGUMENT, "namespace is required.")

        no_ssl_verify = False
        if "no_ssl_verify" in u["parameters"] \
           and u["parameters"]["no_ssl_verify"] == 'yes':
            no_ssl_verify = True

        self._c = pywbem.WBEMConnection(
            url, (u['username'], password), namespace)

        if no_ssl_verify:
            try:
                self._c = pywbem.WBEMConnection(
                    url, (u['username'], password), namespace,
                    no_verification=True)
            except TypeError:
                # pywbem is not holding fix from
                # https://bugzilla.redhat.com/show_bug.cgi?id=1039801
                pass

        self.tmo = timeout

    @handle_cim_errors
    def time_out_set(self, ms, flags=lsm.Client.FLAG_RSVD):
        self.tmo = ms

    @handle_cim_errors
    def time_out_get(self, flags=lsm.Client.FLAG_RSVD):
        return self.tmo

    @handle_cim_errors
    def plugin_unregister(self, flags=lsm.Client.FLAG_RSVD):
        self._c = None

    @handle_cim_errors
    def capabilities(self, system, flags=lsm.Client.FLAG_RSVD):
        cap = Capabilities()
        cap.set(Capabilities.VOLUMES)
        return cap

    @handle_cim_errors
    def plugin_info(self, flags=lsm.Client.FLAG_RSVD):
        return "Generic SSM support", VERSION

    @handle_cim_errors
    def job_status(self, job_id, flags=lsm.Client.FLAG_RSVD):
        """
        Given a job id returns the current status as a tuple
        (status (enum), percent_complete(integer), volume (None or Volume))
        """
        raise LsmError(ErrorNumber.NO_SUPPORT, "not support yet")

    @handle_cim_errors
    def job_free(self, job_id, flags=lsm.Client.FLAG_RSVD):
        raise LsmError(ErrorNumber.NO_SUPPORT, "not support yet")

    @handle_cim_errors
    def systems(self, flags=lsm.Client.FLAG_RSVD):
        rc_lsm_syss = []
        ssm_syss = self._c.EnumerateInstances('SSM_StorageSystem')
        for ssm_sys in ssm_syss:
            ## Skip non-root system.
            if 'NULL' not in ssm_sys['ParentSystems']:
                continue
            rc_lsm_syss.append(_ssm_sys_to_lsm(ssm_sys))

        return rc_lsm_syss

    @handle_cim_errors
    def pools(self, search_key=None, search_value=None,
              flags=lsm.Client.FLAG_RSVD):
        rc_lsm_pools = []
        ssm_pools = self._c.EnumerateInstances('SSM_StoragePool')
        for ssm_pool in ssm_pools:
            rc_lsm_pools.append(_ssm_pool_to_lsm(ssm_pool))

        return search_property(rc_lsm_pools, search_key, search_value)

    @handle_cim_errors
    def volumes(self, search_key=None, search_value=None,
                flags=lsm.Client.FLAG_RSVD):
        rc_lsm_vols = []
        ssm_vols = self._c.EnumerateInstances('SSM_StorageVolume')
        for ssm_vol in ssm_vols:
            rc_lsm_vols.append(_ssm_vol_to_lsm(ssm_vol))
        return search_property(rc_lsm_vols, search_key, search_value)

    @handle_cim_errors
    def volume_create(self, pool, volume_name, size_bytes, provisioning,
                      flags=lsm.Client.FLAG_RSVD):
        if provisioning != Volume.PROVISION_FULL and \
           provisioning != Volume.PROVISION_DEFAULT:
            raise LsmError(ErrorNumber.NO_SUPPORT,
                           "Thin provisioning is not supported yet")

        ssm_pool = self._ssm_pool_of_id(pool.id)
        ssm_sys = self._ssm_sys_of_id(pool.system_id)

        try:
            (rc, out) = self._c.InvokeMethod(
                'CreateStorageVolume', ssm_sys.path,
                GivenName=volume_name,
                Size=pywbem.Uint64(size_bytes),
                ProvisioningType='Full',
                InPools=[ssm_pool.path])
        except pywbem.CIMError:
            # Check duplicate name
            vols = self.volumes()
            for v in vols:
                if v.name == volume_name:
                    raise LsmError(ErrorNumber.NAME_CONFLICT,
                                   "Name '%s' is used by other volume" %
                                   volume_name)
            raise
        if rc != 0:
            raise LsmError(ErrorNumber.PLUGIN_BUG,
                           "CreateStorageVolume returned error %d, out: %s" %
                           (rc, out))
        ssm_vol_path = out['TheStorageVolume']
        ssm_vol = self._c.GetInstance(ssm_vol_path)
        return None, _ssm_vol_to_lsm(ssm_vol)

    @handle_cim_errors
    def volume_resize(self, volume, new_size_bytes, flags=lsm.Client.FLAG_RSVD):
        if new_size_bytes < volume.size_bytes:
            raise LsmError(ErrorNumber.NO_SUPPORT,
                           "Volume size shrink is not supported yet")
        elif new_size_bytes == volume.size_bytes:
            return None, volume

        ssm_vol = self._ssm_vol_of_id(volume.id)
        ssm_sys = self._ssm_sys_of_id(volume.system_id)

        (rc, out) = self._c.InvokeMethod(
            'ModifyStorageVolume', ssm_sys.path,
            Size=pywbem.Uint64(new_size_bytes),
            TheStorageVolume=ssm_vol.path,
            InPools=[volume.pool_id])
        if rc != 0:
            raise LsmError(ErrorNumber.PLUGIN_BUG,
                           "ModifyStorageVolume returned error %d, out: %s" %
                           (rc, out))
        ssm_vol = self._c.GetInstance(ssm_vol.path)
        return None, _ssm_vol_to_lsm(ssm_vol)

    @handle_cim_errors
    def volume_delete(self, volume, flags=lsm.Client.FLAG_RSVD):
        ssm_vol = self._ssm_vol_of_id(volume.id)
        ssm_sys = self._ssm_sys_of_id(volume.system_id)
        (rc, out) = self._c.InvokeMethod('DeleteStorageVolume', ssm_sys.path,
                                         TheStorageVolume=ssm_vol.path)
        if rc != 0:
            raise LsmError(ErrorNumber.PLUGIN_BUG,
                           "DeleteStorageVolume returned error %d, out: %s" %
                           (rc, out))

        return None
