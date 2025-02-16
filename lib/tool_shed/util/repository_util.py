import logging
import os
import re

from markupsafe import escape
from sqlalchemy import false
from sqlalchemy.sql import select

import tool_shed.dependencies.repository
from galaxy import util
from galaxy import web
from galaxy.tool_shed.util.repository_util import (
    change_repository_name_in_hgrc_file,
    check_for_updates,
    check_or_update_tool_shed_status_for_installed_repository,
    create_or_update_tool_shed_repository,
    extract_components_from_tuple,
    generate_tool_shed_repository_install_dir,
    get_absolute_path_to_file_in_repository,
    get_ids_of_tool_shed_repositories_being_installed,
    get_installed_repository,
    get_installed_tool_shed_repository,
    get_prior_import_or_install_required_dict,
    get_repo_info_tuple_contents,
    get_repository_admin_role_name,
    get_repository_and_repository_dependencies_from_repo_info_dict,
    get_repository_by_id,
    get_repository_by_name,
    get_repository_by_name_and_owner,
    get_repository_dependency_types,
    get_repository_for_dependency_relationship,
    get_repository_ids_requiring_prior_import_or_install,
    get_repository_in_tool_shed,
    get_repository_owner,
    get_repository_owner_from_clone_url,
    get_repository_query,
    get_role_by_id,
    get_tool_shed_from_clone_url,
    get_tool_shed_repository_by_id,
    get_tool_shed_status_for_installed_repository,
    is_tool_shed_client,
    repository_was_previously_installed,
    set_repository_attributes,
)
from galaxy.util.tool_shed import common_util
from tool_shed.util.hg_util import (
    changeset2rev,
    create_hgrc_file,
    init_repository,
)
from tool_shed.util.metadata_util import (
    get_next_downloadable_changeset_revision,
    get_repository_metadata_by_changeset_revision,
)

log = logging.getLogger(__name__)

VALID_REPOSITORYNAME_RE = re.compile(r"^[a-z0-9\_]+$")


def create_repo_info_dict(app, repository_clone_url, changeset_revision, ctx_rev, repository_owner, repository_name=None,
                          repository=None, repository_metadata=None, tool_dependencies=None, repository_dependencies=None):
    """
    Return a dictionary that includes all of the information needed to install a repository into a local
    Galaxy instance.  The dictionary will also contain the recursive list of repository dependencies defined
    for the repository, as well as the defined tool dependencies.

    This method is called from Galaxy under four scenarios:
    1. During the tool shed repository installation process via the tool shed's get_repository_information()
    method.  In this case both the received repository and repository_metadata will be objects, but
    tool_dependencies and repository_dependencies will be None.
    2. When getting updates for an installed repository where the updates include newly defined repository
    dependency definitions.  This scenario is similar to 1. above. The tool shed's get_repository_information()
    method is the caller, and both the received repository and repository_metadata will be objects, but
    tool_dependencies and repository_dependencies will be None.
    3. When a tool shed repository that was uninstalled from a Galaxy instance is being reinstalled with no
    updates available.  In this case, both repository and repository_metadata will be None, but tool_dependencies
    and repository_dependencies will be objects previously retrieved from the tool shed if the repository includes
    definitions for them.
    4. When a tool shed repository that was uninstalled from a Galaxy instance is being reinstalled with updates
    available.  In this case, this method is reached via the tool shed's get_updated_repository_information()
    method, and both repository and repository_metadata will be objects but tool_dependencies and
    repository_dependencies will be None.
    """
    repo_info_dict = {}
    repository = get_repository_by_name_and_owner(app, repository_name, repository_owner)
    if app.name == 'tool_shed':
        # We're in the tool shed.
        repository_metadata = get_repository_metadata_by_changeset_revision(app,
                                                                            app.security.encode_id(repository.id),
                                                                            changeset_revision)
        if repository_metadata:
            metadata = repository_metadata.metadata
            if metadata:
                tool_shed_url = web.url_for('/', qualified=True).rstrip('/')
                rb = tool_shed.dependencies.repository.relation_builder.RelationBuilder(app, repository, repository_metadata, tool_shed_url)
                # Get a dictionary of all repositories upon which the contents of the received repository depends.
                repository_dependencies = rb.get_repository_dependencies_for_changeset_revision()
                tool_dependencies = metadata.get('tool_dependencies', {})
    if tool_dependencies:
        new_tool_dependencies = {}
        for dependency_key, requirements_dict in tool_dependencies.items():
            if dependency_key in ['set_environment']:
                new_set_environment_dict_list = []
                for set_environment_dict in requirements_dict:
                    set_environment_dict['repository_name'] = repository_name
                    set_environment_dict['repository_owner'] = repository_owner
                    set_environment_dict['changeset_revision'] = changeset_revision
                    new_set_environment_dict_list.append(set_environment_dict)
                new_tool_dependencies[dependency_key] = new_set_environment_dict_list
            else:
                requirements_dict['repository_name'] = repository_name
                requirements_dict['repository_owner'] = repository_owner
                requirements_dict['changeset_revision'] = changeset_revision
                new_tool_dependencies[dependency_key] = requirements_dict
        tool_dependencies = new_tool_dependencies
    repo_info_dict[repository.name] = (repository.description,
                                       repository_clone_url,
                                       changeset_revision,
                                       ctx_rev,
                                       repository_owner,
                                       repository_dependencies,
                                       tool_dependencies)
    return repo_info_dict


def create_repository_admin_role(app, repository):
    """
    Create a new role with name-spaced name based on the repository name and its owner's public user
    name.  This will ensure that the tole name is unique.
    """
    sa_session = app.model.context.current
    name = get_repository_admin_role_name(str(repository.name), str(repository.user.username))
    description = 'A user or group member with this role can administer this repository.'
    role = app.model.Role(name=name, description=description, type=app.model.Role.types.SYSTEM)
    sa_session.add(role)
    sa_session.flush()
    # Associate the role with the repository owner.
    app.model.UserRoleAssociation(repository.user, role)
    # Associate the role with the repository.
    rra = app.model.RepositoryRoleAssociation(repository, role)
    sa_session.add(rra)
    sa_session.flush()
    return role


def create_repository(app, name, type, description, long_description, user_id, category_ids=[], remote_repository_url=None, homepage_url=None):
    """Create a new ToolShed repository"""
    sa_session = app.model.context.current
    # Add the repository record to the database.
    repository = app.model.Repository(name=name,
                                      type=type,
                                      remote_repository_url=remote_repository_url,
                                      homepage_url=homepage_url,
                                      description=description,
                                      long_description=long_description,
                                      user_id=user_id)
    # Flush to get the id.
    sa_session.add(repository)
    sa_session.flush()
    # Create an admin role for the repository.
    create_repository_admin_role(app, repository)
    # Determine the repository's repo_path on disk.
    dir = os.path.join(app.config.file_path, *util.directory_hash_id(repository.id))
    # Create directory if it does not exist.
    if not os.path.exists(dir):
        os.makedirs(dir)
    # Define repo name inside hashed directory.
    repository_path = os.path.join(dir, "repo_%d" % repository.id)
    # Create local repository directory.
    if not os.path.exists(repository_path):
        os.makedirs(repository_path)
    # Create the local repository.
    init_repository(repo_path=repository_path)
    # Add an entry in the hgweb.config file for the local repository.
    lhs = "repos/%s/%s" % (repository.user.username, repository.name)
    app.hgweb_config_manager.add_entry(lhs, repository_path)
    # Create a .hg/hgrc file for the local repository.
    create_hgrc_file(app, repository)
    flush_needed = False
    if category_ids:
        # Create category associations
        for category_id in category_ids:
            category = sa_session.query(app.model.Category) \
                                 .get(app.security.decode_id(category_id))
            rca = app.model.RepositoryCategoryAssociation(repository, category)
            sa_session.add(rca)
            flush_needed = True
    if flush_needed:
        sa_session.flush()
    # Update the repository registry.
    app.repository_registry.add_entry(repository)
    message = "Repository <b>%s</b> has been created." % escape(str(repository.name))
    return repository, message


def generate_sharable_link_for_repository_in_tool_shed(repository, changeset_revision=None):
    """Generate the URL for sharing a repository that is in the tool shed."""
    base_url = web.url_for('/', qualified=True).rstrip('/')
    sharable_url = '%s/view/%s/%s' % (base_url, repository.user.username, repository.name)
    if changeset_revision:
        sharable_url += '/%s' % changeset_revision
    return sharable_url


def get_repo_info_dict(app, user, repository_id, changeset_revision):
    repository = get_repository_in_tool_shed(app, repository_id)
    repository_clone_url = common_util.generate_clone_url_for_repository_in_tool_shed(user, repository)
    repository_metadata = get_repository_metadata_by_changeset_revision(app,
                                                                        repository_id,
                                                                        changeset_revision)
    if not repository_metadata:
        # The received changeset_revision is no longer installable, so get the next changeset_revision
        # in the repository's changelog.  This generally occurs only with repositories of type
        # repository_suite_definition or tool_dependency_definition.
        next_downloadable_changeset_revision = \
            get_next_downloadable_changeset_revision(app, repository, changeset_revision)
        if next_downloadable_changeset_revision and next_downloadable_changeset_revision != changeset_revision:
            repository_metadata = get_repository_metadata_by_changeset_revision(app,
                                                                                repository_id,
                                                                                next_downloadable_changeset_revision)
    if repository_metadata:
        # For now, we'll always assume that we'll get repository_metadata, but if we discover our assumption
        # is not valid we'll have to enhance the callers to handle repository_metadata values of None in the
        # returned repo_info_dict.
        metadata = repository_metadata.metadata
        if 'tools' in metadata:
            includes_tools = True
        else:
            includes_tools = False
        includes_tools_for_display_in_tool_panel = repository_metadata.includes_tools_for_display_in_tool_panel
        repository_dependencies_dict = metadata.get('repository_dependencies', {})
        repository_dependencies = repository_dependencies_dict.get('repository_dependencies', [])
        has_repository_dependencies, has_repository_dependencies_only_if_compiling_contained_td = \
            get_repository_dependency_types(repository_dependencies)
        if 'tool_dependencies' in metadata:
            includes_tool_dependencies = True
        else:
            includes_tool_dependencies = False
    else:
        # Here's where we may have to handle enhancements to the callers. See above comment.
        includes_tools = False
        has_repository_dependencies = False
        has_repository_dependencies_only_if_compiling_contained_td = False
        includes_tool_dependencies = False
        includes_tools_for_display_in_tool_panel = False
    repo_path = repository.repo_path(app)
    ctx_rev = str(changeset2rev(repo_path, changeset_revision))
    repo_info_dict = create_repo_info_dict(app=app,
                                           repository_clone_url=repository_clone_url,
                                           changeset_revision=changeset_revision,
                                           ctx_rev=ctx_rev,
                                           repository_owner=repository.user.username,
                                           repository_name=repository.name,
                                           repository=repository,
                                           repository_metadata=repository_metadata,
                                           tool_dependencies=None,
                                           repository_dependencies=None)
    return repo_info_dict, includes_tools, includes_tool_dependencies, includes_tools_for_display_in_tool_panel, \
        has_repository_dependencies, has_repository_dependencies_only_if_compiling_contained_td


def get_repositories_by_category(app, category_id, installable=False, sort_order='asc', sort_key='name', page=None, per_page=25):
    sa_session = app.model.context.current
    query = sa_session.query(app.model.Repository) \
                      .join(app.model.RepositoryCategoryAssociation, app.model.Repository.id == app.model.RepositoryCategoryAssociation.repository_id) \
                      .join(app.model.User, app.model.User.id == app.model.Repository.user_id) \
                      .filter(app.model.RepositoryCategoryAssociation.category_id == category_id)
    if installable:
        subquery = select([app.model.RepositoryMetadata.table.c.repository_id])
        query = query.filter(app.model.Repository.id.in_(subquery))
    if sort_key == 'owner':
        query = query.order_by(app.model.User.username) if sort_order == 'asc' else query.order_by(app.model.User.username.desc())
    else:
        query = query.order_by(app.model.Repository.name) if sort_order == 'asc' else query.order_by(app.model.Repository.name.desc())
    if page is not None:
        page = int(page)
        query = query.limit(per_page)
        if page > 1:
            query = query.offset((page - 1) * per_page)
    resultset = query.all()
    repositories = []
    for repository in resultset:
        default_value_mapper = {'id': app.security.encode_id, 'user_id': app.security.encode_id, 'repository_id': app.security.encode_id}
        repository_dict = repository.to_dict(value_mapper=default_value_mapper)
        repository_dict['metadata'] = {}
        for changeset, changehash in repository.installable_revisions(app):
            encoded_id = app.security.encode_id(repository.id)
            metadata = get_repository_metadata_by_changeset_revision(app, encoded_id, changehash)
            repository_dict['metadata']['%s:%s' % (changeset, changehash)] = metadata.to_dict(value_mapper=default_value_mapper)
        if installable:
            if len(repository.installable_revisions(app)):
                repositories.append(repository_dict)
        else:
            repositories.append(repository_dict)
    return repositories


def get_tool_shed_repository_status_label(app, tool_shed_repository=None, name=None, owner=None, changeset_revision=None, repository_clone_url=None):
    """Return a color-coded label for the status of the received tool-shed_repository installed into Galaxy."""
    if tool_shed_repository is None:
        if name is not None and owner is not None and repository_clone_url is not None:
            tool_shed = get_tool_shed_from_clone_url(repository_clone_url)
            tool_shed_repository = get_installed_repository(app,
                                                            tool_shed=tool_shed,
                                                            name=name,
                                                            owner=owner,
                                                            installed_changeset_revision=changeset_revision)
    if tool_shed_repository:
        status_label = tool_shed_repository.status
        if tool_shed_repository.status in [app.install_model.ToolShedRepository.installation_status.CLONING,
                                           app.install_model.ToolShedRepository.installation_status.SETTING_TOOL_VERSIONS,
                                           app.install_model.ToolShedRepository.installation_status.INSTALLING_REPOSITORY_DEPENDENCIES,
                                           app.install_model.ToolShedRepository.installation_status.INSTALLING_TOOL_DEPENDENCIES,
                                           app.install_model.ToolShedRepository.installation_status.LOADING_PROPRIETARY_DATATYPES]:
            bgcolor = app.install_model.ToolShedRepository.states.INSTALLING
        elif tool_shed_repository.status in [app.install_model.ToolShedRepository.installation_status.NEW,
                                             app.install_model.ToolShedRepository.installation_status.UNINSTALLED]:
            bgcolor = app.install_model.ToolShedRepository.states.UNINSTALLED
        elif tool_shed_repository.status in [app.install_model.ToolShedRepository.installation_status.ERROR]:
            bgcolor = app.install_model.ToolShedRepository.states.ERROR
        elif tool_shed_repository.status in [app.install_model.ToolShedRepository.installation_status.DEACTIVATED]:
            bgcolor = app.install_model.ToolShedRepository.states.WARNING
        elif tool_shed_repository.status in [app.install_model.ToolShedRepository.installation_status.INSTALLED]:
            if tool_shed_repository.repository_dependencies_being_installed:
                bgcolor = app.install_model.ToolShedRepository.states.WARNING
                status_label = '%s, %s' % (status_label,
                                           app.install_model.ToolShedRepository.installation_status.INSTALLING_REPOSITORY_DEPENDENCIES)
            elif tool_shed_repository.missing_repository_dependencies:
                bgcolor = app.install_model.ToolShedRepository.states.WARNING
                status_label = '%s, missing repository dependencies' % status_label
            elif tool_shed_repository.tool_dependencies_being_installed:
                bgcolor = app.install_model.ToolShedRepository.states.WARNING
                status_label = '%s, %s' % (status_label,
                                           app.install_model.ToolShedRepository.installation_status.INSTALLING_TOOL_DEPENDENCIES)
            elif tool_shed_repository.missing_tool_dependencies:
                bgcolor = app.install_model.ToolShedRepository.states.WARNING
                status_label = '%s, missing tool dependencies' % status_label
            else:
                bgcolor = app.install_model.ToolShedRepository.states.OK
        else:
            bgcolor = app.install_model.ToolShedRepository.states.ERROR
    else:
        bgcolor = app.install_model.ToolShedRepository.states.WARNING
        status_label = 'unknown status'
    return '<div class="count-box state-color-%s">%s</div>' % (bgcolor, status_label)


def handle_role_associations(app, role, repository, **kwd):
    sa_session = app.model.context.current
    message = escape(kwd.get('message', ''))
    status = kwd.get('status', 'done')
    repository_owner = repository.user
    if kwd.get('manage_role_associations_button', False):
        in_users_list = util.listify(kwd.get('in_users', []))
        in_users = [sa_session.query(app.model.User).get(x) for x in in_users_list]
        # Make sure the repository owner is always associated with the repostory's admin role.
        owner_associated = False
        for user in in_users:
            if user.id == repository_owner.id:
                owner_associated = True
                break
        if not owner_associated:
            in_users.append(repository_owner)
            message += "The repository owner must always be associated with the repository's administrator role.  "
            status = 'error'
        in_groups_list = util.listify(kwd.get('in_groups', []))
        in_groups = [sa_session.query(app.model.Group).get(x) for x in in_groups_list]
        in_repositories = [repository]
        app.security_agent.set_entity_role_associations(roles=[role],
                                                        users=in_users,
                                                        groups=in_groups,
                                                        repositories=in_repositories)
        sa_session.refresh(role)
        message += "Role <b>%s</b> has been associated with %d users, %d groups and %d repositories.  " % \
            (escape(str(role.name)), len(in_users), len(in_groups), len(in_repositories))
    in_users = []
    out_users = []
    in_groups = []
    out_groups = []
    for user in sa_session.query(app.model.User) \
                          .filter(app.model.User.table.c.deleted == false()) \
                          .order_by(app.model.User.table.c.email):
        if user in [x.user for x in role.users]:
            in_users.append((user.id, user.email))
        else:
            out_users.append((user.id, user.email))
    for group in sa_session.query(app.model.Group) \
                           .filter(app.model.Group.table.c.deleted == false()) \
                           .order_by(app.model.Group.table.c.name):
        if group in [x.group for x in role.groups]:
            in_groups.append((group.id, group.name))
        else:
            out_groups.append((group.id, group.name))
    associations_dict = dict(in_users=in_users,
                             out_users=out_users,
                             in_groups=in_groups,
                             out_groups=out_groups,
                             message=message,
                             status=status)
    return associations_dict


def update_repository(app, trans, id, **kwds):
    """Update an existing ToolShed repository"""
    message = None
    flush_needed = False
    sa_session = app.model.context.current
    repository = sa_session.query(app.model.Repository).get(app.security.decode_id(id))
    if repository is None:
        return None, "Unknown repository ID"

    if not (trans.user_is_admin or
            trans.app.security_agent.user_can_administer_repository(trans.user, repository)):
        message = "You are not the owner of this repository, so you cannot administer it."
        return None, message

    # Whitelist properties that can be changed via this method
    for key in ('type', 'description', 'long_description', 'remote_repository_url', 'homepage_url'):
        # If that key is available, not None and different than what's in the model
        if key in kwds and kwds[key] is not None and kwds[key] != getattr(repository, key):
            setattr(repository, key, kwds[key])
            flush_needed = True

    if 'category_ids' in kwds and isinstance(kwds['category_ids'], list):
        # Get existing category associations
        category_associations = sa_session.query(app.model.RepositoryCategoryAssociation) \
                                          .filter(app.model.RepositoryCategoryAssociation.table.c.repository_id == app.security.decode_id(id))
        # Remove all of them
        for rca in category_associations:
            sa_session.delete(rca)

        # Then (re)create category associations
        for category_id in kwds['category_ids']:
            category = sa_session.query(app.model.Category) \
                                 .get(app.security.decode_id(category_id))
            if category:
                rca = app.model.RepositoryCategoryAssociation(repository, category)
                sa_session.add(rca)
            else:
                pass
        flush_needed = True

    # However some properties are special, like 'name'
    if 'name' in kwds and kwds['name'] is not None and repository.name != kwds['name']:
        if repository.times_downloaded != 0:
            message = "Repository names cannot be changed if the repository has been cloned."
        else:
            message = validate_repository_name(trans.app, kwds['name'], trans.user)
        if message:
            return None, message

        repo_dir = repository.repo_path(app)
        # Change the entry in the hgweb.config file for the repository.
        old_lhs = "repos/%s/%s" % (repository.user.username, repository.name)
        new_lhs = "repos/%s/%s" % (repository.user.username, kwds['name'])
        trans.app.hgweb_config_manager.change_entry(old_lhs, new_lhs, repo_dir)

        # Change the entry in the repository's hgrc file.
        hgrc_file = os.path.join(repo_dir, '.hg', 'hgrc')
        change_repository_name_in_hgrc_file(hgrc_file, kwds['name'])

        # Rename the repository's admin role to match the new repository name.
        repository_admin_role = repository.admin_role
        repository_admin_role.name = get_repository_admin_role_name(str(kwds['name']), str(repository.user.username))
        trans.sa_session.add(repository_admin_role)
        repository.name = kwds['name']
        flush_needed = True

    if flush_needed:
        trans.sa_session.add(repository)
        trans.sa_session.flush()
        message = "The repository information has been updated."
    else:
        message = None
    return repository, message


def validate_repository_name(app, name, user):
    """
    Validate whether the given name qualifies as a new TS repo name.
    Repository names must be unique for each user, must be at least two characters
    in length and must contain only lower-case letters, numbers, and the '_' character.
    """
    if name in ['None', None, '']:
        return 'Enter the required repository name.'
    if name in ['repos']:
        return "The term '%s' is a reserved word in the Tool Shed, so it cannot be used as a repository name." % name
    check_existing = get_repository_by_name_and_owner(app, name, user.username)
    if check_existing is not None:
        if check_existing.deleted:
            return 'You own a deleted repository named <b>%s</b>, please choose a different name.' % escape(name)
        else:
            return "You already own a repository named <b>%s</b>, please choose a different name." % escape(name)
    if len(name) < 2:
        return "Repository names must be at least 2 characters in length."
    if len(name) > 80:
        return "Repository names cannot be more than 80 characters in length."
    if not(VALID_REPOSITORYNAME_RE.match(name)):
        return "Repository names must contain only lower-case letters, numbers and underscore."
    return ''


__all__ = (
    'change_repository_name_in_hgrc_file',
    'check_for_updates',
    'check_or_update_tool_shed_status_for_installed_repository',
    'create_or_update_tool_shed_repository',
    'create_repo_info_dict',
    'create_repository_admin_role',
    'create_repository',
    'extract_components_from_tuple',
    'generate_sharable_link_for_repository_in_tool_shed',
    'generate_tool_shed_repository_install_dir',
    'get_absolute_path_to_file_in_repository',
    'get_ids_of_tool_shed_repositories_being_installed',
    'get_installed_repository',
    'get_installed_tool_shed_repository',
    'get_prior_import_or_install_required_dict',
    'get_repo_info_dict',
    'get_repo_info_tuple_contents',
    'get_repositories_by_category',
    'get_repository_admin_role_name',
    'get_repository_and_repository_dependencies_from_repo_info_dict',
    'get_repository_by_id',
    'get_repository_by_name',
    'get_repository_by_name_and_owner',
    'get_repository_dependency_types',
    'get_repository_for_dependency_relationship',
    'get_repository_ids_requiring_prior_import_or_install',
    'get_repository_in_tool_shed',
    'get_repository_owner',
    'get_repository_owner_from_clone_url',
    'get_repository_query',
    'get_role_by_id',
    'get_tool_shed_from_clone_url',
    'get_tool_shed_repository_by_id',
    'get_tool_shed_repository_status_label',
    'get_tool_shed_status_for_installed_repository',
    'handle_role_associations',
    'is_tool_shed_client',
    'repository_was_previously_installed',
    'set_repository_attributes',
    'update_repository',
    'validate_repository_name',
)
