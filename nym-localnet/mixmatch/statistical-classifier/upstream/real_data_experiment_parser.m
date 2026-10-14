import os

databases_path = '../datasets';
experiment_database = 'baseline';

experiment_database_path = databases_path + '/' + experiment_database

experiment_folder_contents = os.listdir(experiment_database_path)


folders_list = []

for content in experiment_folder_contents:
  if len(content) > 80:
    folders_list.append(content)
    
    

cd([databases_path '/' experiment_database]);
folder_contents = dir;
experiment_folders = {};
flow_pairs = {};
for folder_element_index = 1:length(folder_contents)
  if (length(folder_contents(folder_element_index).name) > 80)
    experiment_folders{length(experiment_folders) + 1} = folder_contents(folder_element_index).name;
    cd(folder_contents(folder_element_index).name);
    fh = fopen('flowpair.json', 'r');
    json_text = fgetl(fh, '%s');
    fclose(fh);
  else
    fprintf(1, 'Skipping content of length %3i %s\n', length(folder_contents(folder_element_index).name), folder_contents(folder_element_index).name);
  endif
endfor

clear folder_element_index;
clear folder_contents;

