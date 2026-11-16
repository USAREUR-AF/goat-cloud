# goat-cloud on bare metal

**Status: Designed, not built.** No code for this target exists yet. This page records the design so that the work can be built in small steps and reviewed against it.

## In short

Bare metal has no single API that knows everything:
- **What should exist** is written in an **inventory**: a database such as NetBox or Nautobot, or a file in a repository.
- **What does exist** is seen by **observers**: the machine's management controller, an agent in its operating system, the network, a provisioning system, a hypervisor.

The **device** is the unit that has an owner, a purpose and a lifetime, and its inventory record carries the contract. goat-cloud checks the contract on the record, joins the records to what the observers saw, and reports the differences in both directions.

No component refuses a machine that is plugged in, and no component deletes one. So on this target, removal is mostly a notice and a work order. The strongest automated step is a reversible power-off through the management controller, which is off by default. Much of what goat-cloud finds here is **reported and never acted on** (section 8).

Every fact below about a product, protocol or framework carries a reference `[Mn]` to the sources at the end.

---

## 1. The unit

| Term | Meaning |
|---|---|
| Device | One physical machine or appliance: a server, a switch, a power unit. **The unit.** |
| Virtual machine | A guest on a hypervisor. It carries its own contract, and its host is a device |
| Record | One device's or virtual machine's entry in the inventory |
| Observer | A source that sees what exists |
| Identifier | A value that names the same machine in several sources: serial number, system UUID, MAC address, asset tag |
| Site | A place with its own inventory, network and observers. The report's scope |

Virtual machines are where sprawl happens on this target, because people create them in seconds without touching the inventory. They follow the cloud pattern more closely than devices do (section 7.3).

## 2. How the contract renders

The contract is carried by the inventory record, not by the machine.

| Fact | Database mode (NetBox) | File mode (Ansible inventory or YAML) | Notes |
|---|---|---|---|
| project | custom field, text | host or group variable | |
| environment | custom field, text | variable | |
| managed_by | custom field, selection | variable | |
| lifecycle | custom field, selection | variable | Distinct from the record's `status`, which is the report's `state` |
| expires_at | custom field, date and time | variable, ISO 8601 | The return, lease-end or decommission date |
| repository | custom field, URL | the inventory repository, or a variable | Required only when `managed_by` names a tool |
| owner | the record's tenant | variable | **Required on this target** |
| contact | the contact assigned to the tenant or device | variable | |
| cost_center | custom field, text | variable | |
| protected | tag `protected` | `protected: true` | |

NetBox custom fields can be required, can be selections, and can carry a regular expression. All custom values appear under `custom_fields` in the API [M4]. A record has at most one tenant [M3], and contacts are assigned to records through a role [M6]. goat-cloud generates the custom-field definitions from `contract.yaml` as an importable file. In file mode, a schema check runs on every pull request to the inventory repository.

### 2.1 What the contract and report need for this target

Contract 1.0 already has the `custom-field` and `inventory-record` carriers, a per-rendering `unit` and `required` list, and `ansible` in `managed_by`. Its `expires_at` already reads "removed, returned or decommissioned". Report schema 2.0 already knows the `baremetal` platform, the `amortized` cost basis and a document `currency`.

The rest are additions that a later minor version allows:

| Where | Addition | Reason |
|---|---|---|
| `contract.yaml` | A `baremetal` rendering with `unit: device` and a `required` list naming `owner`, not `repository` | A purchased device has no defining repository |
| `contract.yaml` | A fact's key may name a built-in field of the record (`tenant`, `contact`) | Owner and contact are not key-value pairs here [M3][M6] |
| `contract.yaml` | `maas`, `ironic`, `metal3`, `tinkerbell`, `foreman` in `managed_by.allowed` | The provisioning tools |
| Report schema | Scope type `site` | — |
| Report schema | Optional `identifiers` (`serial`, `uuid`, `mac`, `asset_tag`, `hostname`) and `sources` on records | The join is on identifiers, and a reader needs to know which sources saw a machine |
| Report schema | Optional `observed_at` on records and scopes | A disconnected site reports late |
| Report schema | Optional `lifecycle_dates` (`warranty_end`, `support_end`, `lease_end`) | These are not the contract's `expires_at` |
| Report schema | Optional `hosted_on` on records | A virtual machine is hosted on a device |
| Report schema | A rule that tag values are strings and a valueless tag is a key with an empty value | NetBox tags have no value [M7] |

### 2.2 Report fields

| Field | Bare-metal value |
|---|---|
| platform | `baremetal` |
| scope | The site |
| location | Rack and position, or room |
| resource_id | The inventory's identifier for the record; for an undeclared machine, `serial:<value>` or `mac:<value>` |
| resource_type | `device/<role>` or `virtual-machine/<hypervisor>` |
| name | Record name, else the observed hostname |
| tags | Custom-field values as strings; valueless tags as keys with an empty value |
| state | Inventory status, with the observed power state beside it |
| created_at | Purchase or commissioning date; for an undeclared machine, when first observed |
| estimated_monthly_cost | Section 5, with `cost_basis: amortized` |

## 3. Inventory

Each source answers some questions and cannot answer others.

| Source | Answers | Cannot answer |
|---|---|---|
| Inventory database (NetBox, Nautobot) | What should exist, where, who it is dedicated to, its status, who to contact | Whether the machine is powered, connected or still there |
| Inventory file (Ansible inventory, YAML in a repository) | What automation configures, in which groups; history through version control | Anything not written in it |
| Provisioning system (MAAS, Ironic, Metal3, Tinkerbell, Foreman) | What has been installed, for whom, in which state | Machines it did not enroll |
| Management controller (BMC) through Redfish | What physically exists: maker, model, serial number, power state, power draw | Who owns it, or what it is for |
| Operating-system agent (osquery, Fleet) | Hostname, hardware serial, software | Machines with no agent, or powered off |
| Network (LLDP, DHCP leases, switch tables) | What is connected, including what nobody declared | What a connected thing is for |
| Hypervisor (Proxmox, libvirt, VMware) | Which virtual machines run on which host, with their tags | Anything about the hardware beyond its host |

Details the readers rely on:
- **NetBox device record.** Name, role, device type, serial number, asset tag, site, rack and position, status, platform, addresses [M1]. Status is one of `offline`, `active`, `planned`, `staged`, `failed`, `inventory`, `decommissioning` [M2]. The API accepts read-only tokens restricted to client addresses, and lists are paged [M5]. Every change is logged with user, time and before-and-after [M8].
- **Nautobot.** It has the same device model, and its Device Lifecycle Management app records end-of-life and end-of-support dates and support contracts [M9][M10].
- **Ansible inventory.** Hosts in nested groups with variables per host and group, in INI or YAML, often with `group_vars/` and `host_vars/`. Inventory plugins build it from other systems, including one that reads NetBox [M11][M12].
- **Redfish.** `ComputerSystem` has read-only `SerialNumber`, `UUID`, `Manufacturer`, `Model` and `PowerState`, and a writable `AssetTag` [M19]. Its `Reset` action takes `On`, `GracefulShutdown`, `ForceOff` and others [M20]. A chassis's `EnvironmentMetrics` reports `PowerWatts` and `EnergykWh` [M21].
- **osquery.** Its `system_info` table has `hostname`, `uuid` and `hardware_serial` [M22]. Fleet manages osquery across many hosts [M23].
- **LLDP.** `lldpd` advertises a chassis identifier, system name and port to the neighboring switch, and `lldpcli` prints neighbors in JSON [M24].

The scanner runs at the site, on a host the site already has. It reads one inventory and one or more observers, with these identities:
- a read-only inventory token;
- a read-only Redfish account on each controller;
- read access to lease files or to the provisioner's API.

An inventory that cannot be read to its last page produces no report.

**Disconnected sites.** File mode needs only a clone of the inventory repository. Database mode keeps the last successful export of the site's records and scans against it when the database cannot be reached, marking the report with the export's age. The report is a file, carried out by whatever path the site has. Each scope and record carries `observed_at`. The central merge keeps the newest report per site, and a site whose newest report is too old is a finding. A site that has not reported is shown as stale, never as clean.

## 4. Ownership

**The owner is a team, recorded once as the record's tenant.** A device dedicated to one team has that team as tenant. A shared device has the site owner's team.

**The join.** Every source yields rows with identifiers. Rows are matched on serial number first, then system UUID, then MAC address.

| Result | Meaning | Finding |
|---|---|---|
| In the inventory and observed | Normal | none; contract and lifecycle rules apply |
| Observed, not in the inventory | Something connected or powered that nobody declared | unclaimed |
| In the inventory as `active`, observed by no source for N days | The record is stale, or the machine is gone | missing |
| In the inventory as `decommissioning` or `inventory`, observed powered on | Still running after it was meant to stop | lifecycle |
| Two records share an identifier, or one machine matches two records | The inventory contradicts itself | contract |

**The provisioner as a second opinion.** MAAS shows an owner and a resource pool per machine [M13]. Ironic records an `owner` project and a `lessee` project [M15]. The scanner reports deployed machines with no owner in the provisioner, and owners that disagree with the inventory's tenant.

**The controller as the link on the machine.** The management controller is the only observer that answers when the operating system is off. Its writable `AssetTag` is the only place on the machine itself that can carry a link to the record [M19]. The team's provisioning step sets it to the device's inventory identifier, and the scanner compares the two. The scanner never writes it.

## 5. Cost

There is no bill. Cost per device is computed from figures the site owner enters, and shown per owner (showback).

| Component | Formula | Input from |
|---|---|---|
| Hardware | purchase price ÷ service life in months, until the service life ends | Custom fields `purchase_price`, `purchase_date`, `service_life_months` [M4] |
| Electricity | average watts × hours in the month × price per kWh × a site overhead factor | `PowerWatts` from Redfish [M21], else a rated value on the device type; a per-site price sheet |
| Space | rack units × price per unit per month | Rack position [M1]; price sheet |
| Support | contract cost ÷ contract months | Contract record [M10] or a custom field |

- A device with no purchase data is "cost unknown", not zero.
- Every amount carries `cost_basis: amortized` and the document's currency.
- Idle capacity is shown as the site owner's cost and is not spread across teams. That covers machines in `inventory` status and pool machines not allocated to anyone.
- An alert goes out when an owner's monthly total passes a threshold the site owner sets.
- For export, column names follow the FOCUS data-center guidance: a server is the resource, a site the region, the amortized cost the effective cost [M33].
- For a Kubernetes cluster on these machines, the device costs become the price sheet that the [Kubernetes target](kubernetes.md) and OpenCost use [M34].

## 6. Expiry

Anything on loan, on lease, in a lab or on trial is `temporary` and states when it goes back. These are the machines that otherwise stay powered for years. The contract rule `temporary-requires-expires-at` applies.

Hardware also has dates the clouds do not: a warranty end, a maker's end-of-support date, a lease end. Nautobot's lifecycle app records end-of-support dates [M10]; NetBox holds them as custom date fields [M4]. A device past its end of support, or past its service life and still powered, is a lifecycle finding. Its monthly electricity and space cost is attached.

At expiry the scanner dispatches the decommission workflow in the repository named by the contract. Where there is no repository, it sends the notice and opens a work order addressed to the owner. The team's procedure, kept in its repository:
- releases the machine in the provisioner (MAAS release with disk erase, Ironic `undeploy` and cleaning) [M13][M14];
- sets the record to `decommissioning`;
- opens the work order for physical removal.

goat-cloud asks for the procedure and watches for the result. The finding closes only when the record has left `active` and no observer sees the machine.

## 7. Enforcement

### 7.1 Prevention

There is no moment of creation at which the contract can be enforced on the machine: a machine is created by a purchase and a cable, and no API sees either. So the declaration comes first, and the contract is enforced on the record:
- **Database mode.** Required custom fields refuse an incomplete record [M4].
- **File mode.** The pull request check refuses it.

The join finds a connected machine with no record within one scan interval. CIS safeguard 1.2 asks for unauthorized assets to be addressed weekly [M26].

### 7.2 The removal ladder

What "remove" means is chosen per site. The default is the lowest rung.

| Rung | Action | Reversible | Who acts | In the design |
|---|---|---|---|---|
| 0 | Notice to the owner and the site owner; a work order | — | goat-cloud | **Default** |
| 1 | The record's status is set to `decommissioning` | Yes | The owner's procedure | Yes, by dispatch |
| 2 | Power off through the management controller: `GracefulShutdown`, then `ForceOff` after a timeout [M20] | Yes: `On` | goat-cloud, from inside the site, after approval | Optional, off by default |
| 3 | Deprovision through the provisioner, erasing disks [M13][M14] | No | The owner's procedure only | Never by goat-cloud |
| 4 | Network access revoked at the switch port or DHCP | Yes | The site owner | Named in the work order only |
| 5 | Physical removal | No | A person | Work order only |

Rung 2 is refused unless all of the following hold:
- the device's lifecycle is `temporary` or `experiment`, or the device has no record at all;
- it is not marked protected;
- no hypervisor reports guests on it;
- its observation in this run is fresh;
- the approval names this device's serial number;
- the run acts on no more than a set number of devices.

Every action defaults to dry run. An undeclared device is never powered off on the first finding, because an unknown machine may be the one that matters most. It goes through the full warning and grace period, with the site owner as the contact.

The site owner marks as protected the network, storage, power and management devices, and whatever hosts the inventory and the scanner.

The power-off account is limited to power control on one site's machines and is run from inside the site. No action above rung 0 is taken from outside a site, or on observations older than one scan interval.

### 7.3 Virtual machines

An undeclared or expired guest follows the cloud pattern. It is stopped (reversible); after warning, grace and approval, its owner's procedure removes it.
- **Proxmox.** The contract is written as tags of the form `key.value` and a structured description. Its `protection` flag "will disable the remove VM and remove disk operations" and is treated as the native equivalent of a lock [M29]. `user-tag-access` can restrict users to a list of allowed tags [M30].
- **libvirt.** The contract is written in a `metadata` element under its own XML namespace [M31].
- **VMware vSphere.** Tags belong to categories [M32].

## 8. Reported, never acted on

goat-cloud reports these and takes no action on them. A person decides.

| Finding | Why no action |
|---|---|
| A machine observed and not declared | It may be the one that matters most; it gets the full warning and grace with the site owner as contact. Rung 2 needs approval |
| A record `active` but observed by no source | The record may be stale, or the machine gone; only a person can tell |
| Two records sharing an identifier | The inventory contradicts itself |
| A device with no tenant, no identifier, or a missing contract fact | A record correction |
| A provisioner owner that disagrees with the tenant | A record correction |
| A controller asset tag that does not match the record | Set by the team's provisioning step, never by the scanner |
| A device past its end of support or service life | A purchasing and planning decision |
| Deprovisioning and disk erase (rung 3) | Destroys data; only the owner's procedure does it |
| Revoking network access (rung 4) | The site owner's network, named in the work order |
| Physical removal (rung 5) | A person with access to the room |
| A machine with no controller, no agent and no network presence | Nothing observes it; only "declared, not seen" and the periodic physical check the frameworks require can find it [M26] |

## 9. Build standard

What the site owner sets up once:

| # | Set up once | Database mode | File mode |
|---|---|---|---|
| S1 | One inventory is named as the site's declaration | The NetBox or Nautobot instance and site [M1] | One repository and path [M11] |
| S2 | The inventory can hold the contract | Custom fields generated from the contract; `lifecycle`, `project`, `environment` required [M4] | A schema for host variables, checked on pull requests |
| S3 | Owner and contact have one home | Tenant and assigned contact [M3][M6] | `owner` and `contact` variables |
| S4 | A read-only scanner identity per source | A token with write disabled and an address restriction [M5] | Read access to the repository |
| S5 | At least one observer | A read-only Redfish account per controller, a DHCP lease export, or the provisioner's API [M19] | The same |
| S6 | Devices that must never be acted on are marked | A `protected` tag [M7] | `protected: true` |
| S7 | Cost inputs exist | Custom fields and a per-site price sheet | Variables and a file |

The rules a team follows:

| # | Rule | Checked by |
|---|---|---|
| 1 | Every device has a record before it is connected, and the record carries the contract | Required fields or the pull request check; the join finds what has no record |
| 2 | The record holds the identifiers observers will see: always the serial number, and the system UUID and port MAC addresses where known | The scanner reports records with none, and identifiers on two records |
| 3 | The owner is a team, recorded once as the tenant | Contract findings |
| 4 | The record's status is kept true: `planned`, `active`, `offline` or `failed`, `decommissioning`, `inventory` [M2] | The join, against power state and network presence |
| 5 | Anything on loan, on lease, in a lab or on trial is `temporary` and states when it goes back | Lifecycle findings |
| 6 | Machines installed by a provisioner are installed for a named owner [M13][M15] | The scanner compares the provisioner's owner with the tenant |
| 7 | A virtual machine carries its own contract, and its host is declared [M29][M31] | The scanner lists guests per hypervisor |
| 8 | The management controller is reachable, has its own record, and its asset tag is the device's inventory identifier [M19] | The scanner reads and compares `AssetTag` |
| 9 | The team keeps a decommission procedure in its repository | The scanner confirms it exists where the contract names a repository |

The control frameworks start with inventory:
- CIS Control 1 asks to "actively manage (inventory, track, and correct) all enterprise assets" [M25].
- CIS asks for active discovery daily and for unauthorized assets to be addressed weekly [M26].
- NIST CSF 2.0 ID.AM-08 asks that hardware and software be "managed throughout their life cycles" [M28].

## 10. Compared with installing NetBox

NetBox records what an organization intends. A team that keeps it accurate already has rules 1 to 5. goat-cloud adds:

1. A check that the records match reality, in both directions, against sources the open-source NetBox does not read itself.
2. A lifetime that is acted on: a warning before the date, a grace period, an approval, a tracked request to the owner.
3. Cost per owner from the records.
4. The same contract, findings and dashboard as the cloud accounts and clusters.
5. A mode that needs no database: an inventory file and one observer.

It does not add a data model for racks, cables or addresses, discovery protocols of its own, or provisioning. Where an estate has no inventory, file mode is a starting point, not a substitute for one.

## 11. What it costs to run

The scanner is a scheduled job on a host the site already has, reading APIs and files. The inventory database, where one exists, is the site's own; goat-cloud does not install it.

## 12. Build order

| Step | Content | Done when |
|---|---|---|
| B1 | Contract and schema additions of section 2.1 | `contract-gen --check` passes; cloud fixtures still validate; a bare-metal document validates |
| B2 | File-mode inventory reader and schema check | A ten-host example becomes ten valid records; a host with no `lifecycle` fails |
| B3 | Contract findings for records | Complete, incomplete and ownerless records land in the right category |
| B4 | Redfish reader, read-only | Recorded responses from three makers' controllers map to observations |
| B5 | The join and both findings | Eight records and nine observations yield one unclaimed, one missing, no false match |
| B6 | Lifecycle findings and notices, including end of support | An expired record is listed; dry run prints the notice |
| B7 | Per-site report, central merge, staleness finding | Two site documents of different ages merge; the older site is flagged |
| B8 | NetBox reader and generated custom-field definitions | The definitions import into a NetBox run in a container; the token has write disabled |
| B9 | DHCP lease reader | One unknown MAC becomes an unclaimed finding |
| B10 | Cost and per-owner alert | Fixture inputs give the expected monthly figure per owner |
| B11 | Decommission dispatch and reference playbook | A dispatched run sets a record to `decommissioning` |
| B12 | Power-off with every guard of section 7.2, dry run by default | Each guard has a fixture that makes it refuse; one test machine powers off and on |
| B13 | Hypervisor reader, Proxmox first | Recorded responses map to virtual-machine records with `hosted_on` |
| B14 | One provisioner reader | Recorded responses map deployment state and owner |
| B15 | osquery or Fleet reader | Recorded results map to observations with serial and UUID |
| B16 | Nautobot reader, with lifecycle dates | Recorded responses map to records with `lifecycle_dates` |

Validation uses no hardware except in B4 and B12, which each need one real machine with a management controller. Everything else uses recorded API responses, a plain inventory file, or NetBox in a container.

## 13. Open questions

1. **Which inventory first.** The design recommends file mode first: it needs no service, works at a disconnected site, and makes `repository` meaningful. NetBox second.
2. **Which observer first.** The design recommends Redfish first, because it answers when the machine is off. DHCP leases second, because they see what nobody declared.
3. **How far up the ladder.** Whether rung 2 is wanted at all, or removal stays at notice and work order.
4. **Virtual machines.** Whether guests are part of the first bare-metal work or a target of their own.
5. **Which provisioner** for step B14.
6. **What counts as a device.** Servers first, or switches, power units and storage as well.
7. **Cost inputs.** Who enters purchase price, service life and site prices, and in which currency.
8. **Stale sites.** After how many days without a report a site is a finding.

## 14. Not yet confirmed

These statements are used above. They are to be confirmed while building:
- Which makers' management controllers implement `EnvironmentMetrics`, and whether older ones expose only the deprecated Power resource.
- The allowed characters in Proxmox tags, on which the `key.value` form depends [M30].
- The full set of MAAS machine states and what release does to pool membership [M13].
- Whether power units expose per-outlet power through Redfish or SNMP.
- No value for the electricity overhead factor is assumed; it is an input.

## Sources

- [M1] NetBox device model: https://netboxlabs.com/docs/netbox/models/dcim/device/
- [M2] NetBox status choices (source): https://raw.githubusercontent.com/netbox-community/netbox/main/netbox/dcim/choices.py
- [M3] NetBox tenancy: https://netboxlabs.com/docs/netbox/features/tenancy/
- [M4] NetBox custom fields: https://netboxlabs.com/docs/netbox/customization/custom-fields/
- [M5] NetBox REST API: https://netboxlabs.com/docs/netbox/integrations/rest-api/
- [M6] NetBox contacts: https://netboxlabs.com/docs/netbox/features/contacts/
- [M7] NetBox tags: https://netboxlabs.com/docs/netbox/models/extras/tag/
- [M8] NetBox change logging: https://netboxlabs.com/docs/netbox/features/change-logging/
- [M9] Nautobot device: https://docs.nautobot.com/projects/core/en/stable/user-guide/core-data-model/dcim/device/
- [M10] Nautobot Device Lifecycle Management app: https://docs.nautobot.com/projects/device-lifecycle/en/latest/user/app_overview/
- [M11] Ansible inventory: https://docs.ansible.com/ansible/latest/inventory_guide/intro_inventory.html
- [M12] NetBox inventory plugin for Ansible: https://docs.ansible.com/ansible/latest/collections/netbox/netbox/nb_inventory_inventory.html
- [M13] MAAS machine basics: https://canonical.com/maas/docs/about-machine-basics
- [M14] Ironic provision states: https://docs.openstack.org/ironic/latest/user/states.html
- [M15] Ironic node multi-tenancy: https://docs.openstack.org/ironic/latest/admin/node-multitenancy.html
- [M19] Redfish ComputerSystem schema: https://redfish.dmtf.org/schemas/v1/ComputerSystem.v1_22_0.json
- [M20] Redfish Resource schema, ResetType: https://redfish.dmtf.org/schemas/v1/Resource.json
- [M21] Redfish EnvironmentMetrics schema: https://redfish.dmtf.org/schemas/v1/EnvironmentMetrics_v1.xml
- [M22] osquery `system_info` table (source): https://raw.githubusercontent.com/osquery/osquery/master/specs/system_info.table
- [M23] Fleet concepts: https://fleetdm.com/docs/get-started/anatomy
- [M24] lldpd usage: https://lldpd.github.io/usage.html
- [M25] CIS Control 1: https://www.cisecurity.org/controls/inventory-and-control-of-enterprise-assets
- [M26] CIS Controls Assessment Specification, Control 1: https://cas.docs.cisecurity.org/en/latest/source/Controls1/
- [M28] NIST CSF 2.0 ID.AM (reference reproduction): https://csf.tools/reference/nist-cybersecurity-framework/v2-0/id/id-am/
- [M29] Proxmox `qm` manual: https://pve.proxmox.com/pve-docs/qm.1.html
- [M30] Proxmox `datacenter.cfg`: https://pve.proxmox.com/wiki/Manual:_datacenter.cfg
- [M31] libvirt domain XML: https://libvirt.org/formatdomain.html
- [M32] vSphere tagging category cardinality (API reference): https://developer.broadcom.com/xapis/vsphere-automation-api/9.0/data-structures/Vcenter%20Tagging%20Categories%20Info%20Cardinality
- [M33] FOCUS for data center: https://focus.finops.org/technology-categories/focus-for-data-center/
- [M34] OpenCost on-premises pricing: https://opencost.io/docs/configuration/on-prem
