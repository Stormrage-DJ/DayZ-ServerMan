"""Independent literal copies of Product Owner texts (QA-10.31-F3).

A wording change must fail a test. The QA entry of 2026-10-17 13:36:53 in evidence 10.31 checked the copies against
the Product Owner entries.
"""

from __future__ import annotations

RESTORE_EDITOR = {'APPLIED_CORRUPT': 'The current last Apply record of this profile is damaged. The restore keeps it under a dated '
                    'name and does not use it.',
 'APPLIED_MISMATCH': 'The last Apply record of the backup does not match the restored mission files. The restore '
                     'invalidates it. Apply then needs adoption of the mission.',
 'BASELINE_CORRUPT': 'The current working baseline of this profile is damaged. The restore keeps it under a dated '
                     'name and installs or builds a new one.',
 'CARRIED': 'This backup holds the map editor records of this profile. The restore replaces the map plan, the '
            'working baseline and the last Apply record of this profile with the ones of the backup.',
 'CONTENT_CORRUPT': 'A damaged file of the map editor records of this profile is kept under a dated name. The '
                    'restore does not use it.',
 'EDITOR_NOTES_HEADING': 'Map editor records',
 'KEEPS_ORIGINAL': 'The restore keeps the current saved original. If the restored files differ from what the last '
                   'Apply wrote, the restore invalidates the last Apply record, and Apply then needs adoption of the '
                   'mission.',
 'NO_ORIGINAL': 'DayZ-ServerMan has no saved original for this mission. The restore installs the saved original of '
                'the backup.',
 'NO_RECORDS': 'This backup has no map editor records.',
 'ORIGINAL_CORRUPT': {'mission': 'The saved original of the mission is not valid. The restore leaves it as it is and '
                                 'does not restore the map editor records for the mission. The server files of the '
                                 'backup are restored.',
                      'runtime': 'The saved original of the runtime profile folder is not valid. The restore leaves '
                                 'it as it is and does not restore the map editor records for the runtime profile '
                                 'folder. The server files of the backup are restored.'},
 'PLAN_SET_ASIDE': 'The current map plan is not valid. The restore keeps it under a dated name.',
 'REMNANT_MOVED': 'Map editor records of an earlier profile with this ID were moved to the folder for deleted '
                  'records.',
 'REMNANT_NOTE': 'Map editor records of an earlier profile with this ID are still in the DayZ-ServerMan folder. An '
                 'interrupted deletion left them. The restore moves them to the folder for deleted records before it '
                 'starts, and it does not use them.',
 'RESTORED_COPY': 'The saved original of this mission differs from the saved original of the backup, and other '
                  'profiles use this mission: {profiles}. The current original stays active for all profiles. The '
                  'restore keeps the original of the backup as an inactive copy for this profile.',
 'RUNTIME_NO_ORIGINAL': 'DayZ-ServerMan has no saved original for the runtime profile folder of this profile. The '
                        'restore installs the saved original of the backup.',
 'RUNTIME_RESTORED_COPY': 'The saved original of the runtime profile folder differs from the saved original of the '
                          'backup, and other profiles use this runtime profile folder: {profiles}. The current '
                          'original stays active for all profiles. The restore keeps the original of the backup as '
                          'an inactive copy for this profile.',
 'RUNTIME_SHARED': 'Other profiles use this runtime profile folder or have map editor records for it: {profiles}. '
                   'The restore does not change their records.',
 'RUNTIME_SUPERSEDED': 'The saved original of the runtime profile folder differs from the saved original of the '
                       'backup. The restore makes the original of the backup the active original. It keeps the '
                       'current original under a dated name. Nothing is deleted.',
 'SHARED': 'Other profiles use this mission or have map editor records for it: {profiles}. The restore does not '
           'change their records.',
 'SKIPPED': {'mission_key': 'The mission of this backup is not the mission that this profile uses now. The restore '
                            'does not restore the map editor records of this backup.',
             'mission_unresolved': 'DayZ-ServerMan cannot find the mission folder of this profile. The restore does '
                                   'not restore the map editor records of this backup.',
             'path_change': 'The mission path of this profile changed. The restore does not restore the map editor '
                            'records of this backup, and it does not change the current ones. Adopt the mission to '
                            'end the path change.',
             'runtime_key': 'The runtime profile folder of this backup is not the one that this profile uses now. '
                            'The restore restores the map editor records of the mission, but not the saved original '
                            'of the runtime profile folder.'},
 'SUPERSEDED': 'The saved original of this mission differs from the saved original of the backup. The restore makes '
               'the original of the backup the active original. It keeps the current original under a dated name. '
               'Nothing is deleted.'}

ADOPTION = {'ADOPTION_COMMITTED_TEXT': 'Adoption reached its commit point, but a later step failed. No mission file changed. '
                            'The map editor records can have changed. Preview the adoption again to see what is left '
                            'to adopt.',
 'ADOPTION_STALE_TEXT': 'The profile, the settings, the mission files or the map editor records changed after the '
                        'preview. Nothing was adopted. Preview the adoption again and check it before you adopt.',
 'CAPTURE': 'capture',
 'COPY': 'copy',
 'FILES_DIFFER_TEXT': {'copy': 'Adoption is not possible. {path} differs from the saved original and from the Apply '
                               'output of {old_path}, so DayZ-ServerMan cannot show that this mission is a copy. It '
                               'does not declare the current files as the original.',
                       'move': 'Adoption is not possible. {path} differs from the saved original and from the Apply '
                               'output of {old_path}, so DayZ-ServerMan cannot show that the files moved with the '
                               'mission. It does not declare the current files as the original.'},
 'HAPPENED': {'copy': '{old_path} still exists or another profile uses it, so DayZ-ServerMan treats {new_path} as a '
                      'copy. Adoption gives {new_path} its own map editor records.',
              'move': 'This mission moved from {old_path} to {new_path}. Adoption gives {new_path} the map plan and '
                      'the map editor records of this profile. The old folder of records is kept as history.',
              'restore': 'A restore removed the last Apply record of this profile for this mission. Adoption ends '
                         'this state. It keeps the map plan and the saved original.',
              'retired': 'This mission is back at {new_path}. An earlier adoption retired the records of that path. '
                         'Adoption keeps them as history and makes the records of {other_path} active for '
                         '{new_path}.',
              ('copy', 'capture'): 'The files of {new_path} match map editor output for {old_path}, so '
                                   'DayZ-ServerMan treats {new_path} as a copy. Adoption gives {new_path} its own '
                                   'map editor records.',
              ('copy', 'records_only'): '{old_path} still exists or another profile uses it, so DayZ-ServerMan '
                                        'treats {new_path} as a copy. No Apply wrote files for this profile at the '
                                        'old path, so there is no saved original to copy. Adoption gives {new_path} '
                                        'the map plan of this profile.'},
 'INTRO': {'copy': 'Adoption writes these records for {new_path}. It retires the old folder of records of this '
                   'profile and keeps it as history. The records of other profiles stay unchanged.',
           'move': 'Adoption copies these records of this profile to {new_path}. It retires the old folder of '
                   'records. That folder stays as history and is never applied.',
           'restore': 'Adoption closes the invalidated record. It writes no original, no map plan and no Apply '
                      'record.',
           'retired': 'Adoption writes these records. The old folder of records stays under a dated name and is '
                      'never applied.',
           ('copy', 'capture'): 'Adoption writes these records for {new_path}. The records for {old_path} stay '
                                'unchanged.',
           ('copy', 'records_only'): 'Adoption writes this record for {new_path}. It retires the old folder of '
                                     'records of this profile and keeps it as history.'},
 'KEPT_DIFFERENT': 'different',
 'KEPT_SAME': 'same',
 'MOVE': 'move',
 'NOTHING_TO_ADOPT_TEXT': 'This mission needs no adoption. Its map plan and records already belong to the current '
                          'mission path.',
 'NOTICE_CAPTURE': 'The files of this mission match map editor output for {old_path}. Apply is not possible until '
                   'you adopt the mission.',
 'NOTICE_PATH_CHANGE': 'The files of this mission match map editor output for {old_path}, which still exists or '
                       'which another profile uses. Apply is not possible until you adopt the mission.',
 'NO_FILE_NAME': 'A mission file',
 'NO_SOURCE_TEXT': 'Adoption is not possible. DayZ-ServerMan finds no earlier mission path with map editor records '
                   'and a saved original that matches this mission.',
 'OTHER_PROFILES': 'These profiles use {old_path} or have map editor records for it. Adoption does not change their '
                   'records or their mission files.',
 'RESTORE': 'restore',
 'RETIRED': 'retired',
 'ROW_EXISTING': 'Existing folder of records for {new_path}: renamed to {history_name}. It is kept as history.',
 'ROW_FRESH': 'Fresh folder of records for {new_path}: the map plan, the working baseline and the last Apply record '
              'of {other_path} are copied to it.',
 'ROW_HISTORY': 'Folder of the retired records: renamed to {history_name}. It is kept as history.',
 'ROW_INVALIDATED': 'Invalidated last Apply record: closed and kept under a dated name.',
 'ROW_KEPT': {'different': 'Saved original for {new_path}: kept. It differs from the saved original of {old_path}, '
                           'so the working baseline is built again from it. The last Apply record is moved only if '
                           'the mission files still match what that Apply wrote.',
              'same': 'Saved original for {new_path}: kept. It already holds the same files as the saved original of '
                      '{old_path}.'},
 'ROW_LEDGER': 'Records of earlier Apply output: {count} repeated for the new path, so that DayZ-ServerMan '
               'recognizes its own output there.',
 'ROW_ORIGINAL': 'Saved original for {new_path}: a copy of the saved original of {old_path}. The copy keeps a link '
                 'to its source. The original of {old_path} stays.',
 'ROW_RECORDS': {'copy': 'Map plan of this profile: copied to {new_path} as the next revision. Working baseline: '
                         'built from the saved original. There is no last Apply record.',
                 'move': 'Map plan, working baseline and last Apply record: copied to the new path.',
                 ('copy', 'capture'): 'Working baseline for {new_path}: built from the saved original. There is no '
                                      'last Apply record. The map plan stays as it is.',
                 ('copy', 'records_only'): 'Map plan of this profile: copied to {new_path} as the next revision.'},
 'ROW_RETIRED': 'Old folder of records of this profile for {old_path}: marked as retired. It keeps its files and is '
                'never applied.',
 'ROW_RETIRED_ORIGINAL': {'active': 'Saved original for {new_path}: the original of {other_path} becomes active. The '
                                    'current original is kept under a dated name.',
                          'installed': 'Saved original for {new_path}: installed from the records of {other_path}.',
                          'shared': 'Saved original for {new_path}: the current original stays active, because other '
                                    'profiles use this mission. The original of {other_path} is kept as an inactive '
                                    'copy for this profile.'},
 'SUCCESS': {'copy': 'Adopted. {new_path} has its own records. The map plan of this profile is copied to it.',
             'move': 'Adopted. The map plan and the records now belong to {new_path}.',
             'restore': 'Adopted. Apply no longer needs adoption for this mission.',
             'retired': 'Adopted. The map plan and the records are active for {new_path} again.',
             ('copy', 'capture'): 'Adopted. {new_path} has its own records. Its map plan stays as it was.',
             ('copy', 'records_only'): 'Adopted. The map plan of this profile is copied to {new_path}.'},
 'UNCHANGED_FILES': 'No mission file changes. Adoption does not write to the DayZ server folder.',
 'UNCHANGED_NOTHING_DELETED': 'Nothing is deleted. Old records stay in their folders.',
 'UNCHANGED_NO_ORIGINAL': 'No saved original exists yet. The first Apply saves one, after the usual check of the '
                          'mission files.',
 'UNCHANGED_OTHER_PROFILES': 'The records of other profiles stay unchanged.',
 'UNCHANGED_PLAN': 'The map plan of this profile stays as it is.',
 'UNCHANGED_SOURCE': 'The saved original and the output records of {old_path}, and the records of other profiles, '
                     'stay unchanged.'}

CLI = {'ADOPTION_LINES': {'copy': 'Adoption is needed: this mission is a copy of another mission path. Run map adopt to '
                            'review it.',
                    'move': 'Adoption is needed: this mission moved. Run map adopt to review it.',
                    'restore': 'Adoption is needed: a restore removed the last Apply record of this profile for this '
                               'mission. Run map adopt to review it.',
                    'retired': 'Adoption is needed: this mission returned to an earlier path. Run map adopt to '
                               'review it.'},
 'APPLIED_CHANGES': 'The saved plan has changes that no Apply has written.',
 'APPLIED_EMPTY': 'The saved plan is empty.',
 'APPLIED_MATCHES': 'The saved plan matches the last Apply.',
 'EXPORT_NOT_WRITTEN': 'The file {path} could not be written correctly. DayZ-ServerMan removed it.',
 'EXPORT_PROTECTED': '{path} is in a folder that DayZ-ServerMan uses: the DayZ server folder, the DayZ-ServerMan '
                     'folder or the backup folder. Name a file in another folder.',
 'LEFT_OUT': '{count} objects were left out.',
 'PLAN_STATES': {'CORRUPT': 'Not valid',
                 'FUTURE_SCHEMA': 'Saved by a newer DayZ-ServerMan',
                 'INVALID_PLAN': 'Not valid',
                 'MISSING': 'No saved plan',
                 'NEWER_PLAN': 'Saved by a newer DayZ-ServerMan',
                 'OTHER_ASSOCIATION': 'The saved plan belongs to another profile or mission.',
                 'VALID': 'Valid'},
 'READ_ONLY_TEXTS': {'adoption_required': 'Apply is not possible: a restore removed the last Apply record of this '
                                          'profile for this mission. Adopt the mission to continue.',
                     'association_mismatch': 'Read only: the saved map plan does not match the mission folder or '
                                             'runtime profile folder of this profile. Correct the profile settings, '
                                             'then load the plan again.',
                     'invalid_saved_data': 'Read only: the saved map plan is not valid. Set aside invalid plan keeps '
                                           'it under a dated name and starts a new, empty plan.',
                     'mission_path_changed': 'Read only: the mission path changed. The map plan shown belongs to an '
                                             'earlier path. Adopt the mission to use its map editor records for the '
                                             'new path.',
                     'returned_to_earlier_path': 'Read only: this mission returned to an earlier path. Adopt the '
                                                 'mission to use its map editor records again.'}}

CLI_HELP = {'map adopt': 'Move or copy the map editor records of a mission that moved, was copied or was restored, after a '
              'review.',
 'map apply': 'Apply the saved map plan to the mission files, after a review.',
 'map copy': 'Replace the saved map plan with the plan of another profile, or merge the two, after a review.',
 'map export': 'Write the saved map plan to a file.',
 'map import': 'Replace the saved map plan with a plan from a file, or merge the two, after a review.',
 'map inspect': 'Show the loot tier and the building capacity at one point, or for one object.',
 'map preview': 'Show what Apply would change in the mission files. Nothing is changed.',
 'map recover': 'Finish or undo an interrupted mission map change.',
 'map restore': 'Restore the original mission files or the previous state, after a review.',
 'map save': 'Save a map plan from a file over the saved map plan.',
 'map set-aside': 'Keep an invalid saved map plan under a dated name and start a new, empty plan, after a review.',
 'map show': 'Show the saved map plan of a profile and the state of its mission.'}

CLI_OPTIONS = {'keep_change': 'Keep the change in this mission file when you apply, by its path as the review prints it. Repeat it '
                'for more files.',
 'replace_file': 'Replace this mission file when you apply, by its path as the review prints it. Repeat it for more '
                 'files.'}
