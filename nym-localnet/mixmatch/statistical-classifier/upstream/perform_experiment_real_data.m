average_mixnet_delay = 170e-3;
time_units = 1e-9;

current_path = pwd();
database_folder_path = '../datasets/baseline';
cd(database_folder_path);
dir_list = dir();
experiment_folders_cell = {};
experiments_count = 0;
for k=1:length(dir_list)
  if (length(dir_list(k).name) > 80)
    experiments_count++;
    experiment_folders_cell{experiments_count} = dir_list(k).name;
  endif
endfor

fprintf(1, 'Number of experiments: %i\n', experiments_count);

if ~exist('data_initiator_from_gateway', 'var')
  for experiment_index = 1:length(experiment_folders_cell)
    cd(experiment_folders_cell{experiment_index});
    data_initiator_from_gateway{experiment_index} = load('payload_initiator_from_gateway.txt')*time_units;
    data_initiator_to_gateway{experiment_index} = load('payload_initiator_to_gateway.txt')*time_units;
    data_responder_from_gateway{experiment_index} = load('payload_responder_from_gateway.txt')*time_units;
    data_responder_to_gateway{experiment_index} = load('payload_responder_to_gateway.txt')*time_units;
    ack_initiator_from_gateway{experiment_index} = load('ack_initiator_from_gateway.txt')*time_units;
    ack_responder_from_gateway{experiment_index} = load('ack_responder_from_gateway.txt')*time_units;
    cd ..
    fprintf(1, '\rLoading experiment files... Progress: %.2f%%', 100*experiment_index/length(experiment_folders_cell))
  endfor
  fprintf(1, '\n');
endif

cd(current_path);

max_sequences_length = 1000;
lengths_vector = 10:10:max_sequences_length;

fprintf(1, 'Computing score matrix...\n'); fflush(1);

% client_subset_index must be 1, ..., client_subsets_count; and client_subsets_count must be a divisor of experiments_count
assert(mod(experiments_count, client_subsets_count) == 0);

client_indexes = (1:(experiments_count/client_subsets_count)) + (client_subset_index - 1)*experiments_count/client_subsets_count;
server_indexes = 1:experiments_count;

if 1 %~exist('score_matrix', 'var')
  score_matrix = zeros(experiments_count/client_subsets_count, experiments_count, length(lengths_vector) + 1);
endif
if 1 %~exist('data_mean_delay', 'var')
  data_mean_delay = zeros(experiments_count/client_subsets_count, experiments_count);
endif
if 1 %~exist('data_min_delay', 'var')
  data_min_delay = zeros(experiments_count/client_subsets_count, experiments_count);
endif
if 1 %~exist('ack_mean_delay', 'var')
  ack_mean_delay = zeros(experiments_count/client_subsets_count, experiments_count);
endif
if 1 %~exist('ack_min_delay', 'var')
  ack_min_delay = zeros(experiments_count/client_subsets_count, experiments_count);
endif

for client_index = client_indexes
  for server_index = server_indexes
    data_transmission_times = sort([data_initiator_to_gateway{client_index}; data_responder_to_gateway{server_index}], 'ascend');
    data_reception_times = sort([data_initiator_from_gateway{client_index}; data_responder_from_gateway{server_index}], 'ascend');
    ack_transmission_times = data_reception_times;
    ack_reception_times = sort([ack_initiator_from_gateway{client_index}; ack_responder_from_gateway{server_index}], 'ascend');
    [aligned_data_transmission_times, aligned_data_reception_times] = align_sequences(data_transmission_times, data_reception_times, average_mixnet_delay);
    [aligned_ack_transmission_times, aligned_ack_reception_times] = align_sequences(ack_transmission_times, ack_reception_times, average_mixnet_delay);
    data_mean_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index) = mean(aligned_data_reception_times - aligned_data_transmission_times);
    ack_mean_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index) = mean(aligned_ack_reception_times - aligned_ack_transmission_times);
    data_min_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index) = min(aligned_data_reception_times - aligned_data_transmission_times);
    ack_min_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index) = min(aligned_ack_reception_times - aligned_ack_transmission_times);
    fprintf(1, '\rClient %4i, Server %4i.\tMin data delay = %7.2f ms;\tMin ack delay: %7.2f ms;\tMean data delay = %7.2f ms;\tMean ack delay: %7.2f ms.\t', client_index, server_index, 1e3*data_min_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index), 1e3*ack_min_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index), 1e3*data_mean_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index), 1e3*ack_mean_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index));
    assert(ack_mean_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index) >= average_mixnet_delay)
    assert(data_mean_delay(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index) >= average_mixnet_delay)
    min_length = min([length(aligned_data_transmission_times); length(aligned_ack_transmission_times)]);
    score_vector = ...
    simplified_sequences_match_loglikelihood(aligned_data_transmission_times(1:min_length), aligned_data_reception_times(1:min_length), 1/(0.050), true) + ...
    simplified_sequences_match_loglikelihood(aligned_ack_transmission_times(1:min_length), aligned_ack_reception_times(1:min_length), 1/(0.050), true);
    for k=1:length(lengths_vector)
      score_matrix(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index, k) = mean(score_vector(1:lengths_vector(k)));
    endfor
    score_matrix(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index, end) = mean(score_vector);
    fprintf(1, 'LLK(%4i) = %.2f. ', min_length, score_matrix(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index, end)); fflush(1);
    if score_matrix(mod(client_index - 1, experiments_count/client_subsets_count) + 1, server_index, end) ~= -Inf
      fprintf(1, '\n');
    else
      if client_index == server_index
	fprintf(1, '\n');
      endif
    endif
    fprintf(1, 'Progress: %.6f%%', 100*((mod(client_index - 1, experiments_count/client_subsets_count) + 1 - 1) + server_index/experiments_count)/(experiments_count/client_subsets_count)); fflush(1);
    fflush(1);
    %break;
  endfor
  %break;
endfor
fprintf(1, '\nDone!\n');

save('-binary', [database_folder_path '/real_data_experiment_' num2str(client_subset_index) '_of_' num2str(client_subsets_count) '.oct'], ...
     'score_matrix', 'data_mean_delay', 'data_min_delay', 'ack_mean_delay', 'ack_min_delay', 'client_indexes');

