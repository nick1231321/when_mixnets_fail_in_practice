function perform_experiment_real_data_alt_delay_characteristic_3parties_unmatched_negatives(client_subset_index, client_subsets_count, data_path, results_path, database_id)

# database_id = 'exp02_nym-binaries-1.0.2_static-http-download_no-client-cover-traffic_filtered-to-start-end-main';

  pkg load statistics

  average_mixnet_delay = 150e-3; % + 4*51.103e-3; This additional delay corresponds to network delays. Comment if local.
  time_units = 1e-9;

  current_path = pwd();
  mixcorr_data_folder = data_path;
  logs_folder = [mixcorr_data_folder '/logs'];
  database_folder_path = [mixcorr_data_folder '/' database_id];
% log_file = [logs_folder '/' database_id '_' num2str(client_subset_index) '_of_' num2str(client_subsets_count) '.log'];
% log_handler = fopen(log_file, 'wt');
% if log_handler < 0
%   fprintf(1, 'Error opening log file %s\n', log_file);
% endif

  cd(database_folder_path);

				% Read partitions
  train_partition_list_file = [database_folder_path '/flowpairs_train.txt'];
  validation_partition_list_file = [database_folder_path '/flowpairs_val.txt'];
  test_partition_list_file = [database_folder_path '/flowpairs_test.txt'];

  train_partition_list_file_handler = fopen(train_partition_list_file, 'rt');
  train_experiment_folders_cell = {};
  while ~feof(train_partition_list_file_handler)
    experiment_folder = fgetl(train_partition_list_file_handler);
    train_experiment_folders_cell{length(train_experiment_folders_cell) + 1} = experiment_folder;
  endwhile
  fclose(train_partition_list_file_handler);
% fprintf(log_handler, 'Number of experiments in train partition: %i\n', length(train_experiment_folders_cell));
  fprintf(1, 'Number of experiments in train partition: %i\n', length(train_experiment_folders_cell));
  train_set_samples_count = length(train_experiment_folders_cell);

  validation_partition_list_file_handler = fopen(validation_partition_list_file, 'rt');
  validation_experiment_folders_cell = {};
  while ~feof(validation_partition_list_file_handler)
    experiment_folder = fgetl(validation_partition_list_file_handler);
    validation_experiment_folders_cell{length(validation_experiment_folders_cell) + 1} = experiment_folder;
  endwhile
  fclose(validation_partition_list_file_handler);
% fprintf(log_handler, 'Number of experiments in validation partition: %i\n', length(validation_experiment_folders_cell));
  fprintf(1, 'Number of experiments in validation partition: %i\n', length(validation_experiment_folders_cell));
  validation_set_samples_count = length(validation_experiment_folders_cell);

  test_partition_list_file_handler = fopen(test_partition_list_file, 'rt');
  test_experiment_folders_cell = {};
  while ~feof(test_partition_list_file_handler)
    experiment_folder = fgetl(test_partition_list_file_handler);
    test_experiment_folders_cell{length(test_experiment_folders_cell) + 1} = experiment_folder;
  endwhile
  fclose(test_partition_list_file_handler);
% fprintf(log_handler, 'Number of experiments in test partition: %i\n', length(test_experiment_folders_cell));
  fprintf(1, 'Number of experiments in test partition: %i\n', length(test_experiment_folders_cell));
  test_set_samples_count = length(test_experiment_folders_cell);

  cd(database_folder_path);

  for experiment_index = 1:length(train_experiment_folders_cell)
    cd(train_experiment_folders_cell{experiment_index});
    data_initiator_from_gateway_train{experiment_index} = load('payload_initiator_from_gateway.txt')*time_units;
    data_initiator_to_gateway_train{experiment_index} = load('payload_initiator_to_gateway.txt')*time_units;
    data_responder_from_gateway_train{experiment_index} = load('payload_responder_from_gateway.txt')*time_units;
    data_responder_to_gateway_train{experiment_index} = load('payload_responder_to_gateway.txt')*time_units;
    ack_initiator_from_gateway_train{experiment_index} = load('ack_initiator_from_gateway.txt')*time_units;
    ack_responder_from_gateway_train{experiment_index} = load('ack_responder_from_gateway.txt')*time_units;
    cd ..
% fprintf(log_handler, '\rLoading train experiment files... Progress: %.2f%%', 100*experiment_index/length(train_experiment_folders_cell))
    fprintf(1, '\rLoading train experiment files... Progress: %.2f%%', 100*experiment_index/length(train_experiment_folders_cell))
  endfor
				% fprintf(log_handler, '\n');
  fprintf(1, '\n');

  % if 0 % Validation set is not used. Substitute 0 by 1 if used.
    for experiment_index = 1:length(validation_experiment_folders_cell)
      cd(validation_experiment_folders_cell{experiment_index});
      data_initiator_from_gateway_validation{experiment_index} = load('payload_initiator_from_gateway.txt')*time_units;
      data_initiator_to_gateway_validation{experiment_index} = load('payload_initiator_to_gateway.txt')*time_units;
      data_responder_from_gateway_validation{experiment_index} = load('payload_responder_from_gateway.txt')*time_units;
      data_responder_to_gateway_validation{experiment_index} = load('payload_responder_to_gateway.txt')*time_units;
      ack_initiator_from_gateway_validation{experiment_index} = load('ack_initiator_from_gateway.txt')*time_units;
      ack_responder_from_gateway_validation{experiment_index} = load('ack_responder_from_gateway.txt')*time_units;
      cd ..
% fprintf(log_handler, '\rLoading validation experiment files... Progress: %.2f%%', 100*experiment_index/length(validation_experiment_folders_cell))
      fprintf(1, '\rLoading validation experiment files... Progress: %.2f%%', 100*experiment_index/length(validation_experiment_folders_cell))
    endfor
				% fprintf(log_handler, '\n');
    fprintf(1, '\n');
  % endif

  for experiment_index = 1:length(test_experiment_folders_cell)
    cd(test_experiment_folders_cell{experiment_index});
    data_initiator_from_gateway_test{experiment_index} = load('payload_initiator_from_gateway.txt')*time_units;
    data_initiator_to_gateway_test{experiment_index} = load('payload_initiator_to_gateway.txt')*time_units;
    data_responder_from_gateway_test{experiment_index} = load('payload_responder_from_gateway.txt')*time_units;
    data_responder_to_gateway_test{experiment_index} = load('payload_responder_to_gateway.txt')*time_units;
    ack_initiator_from_gateway_test{experiment_index} = load('ack_initiator_from_gateway.txt')*time_units;
    ack_responder_from_gateway_test{experiment_index} = load('ack_responder_from_gateway.txt')*time_units;
    cd ..
% fprintf(log_handler, '\rLoading test experiment files... Progress: %.2f%%', 100*experiment_index/length(test_experiment_folders_cell))
    fprintf(1, '\rLoading test experiment files... Progress: %.2f%%', 100*experiment_index/length(test_experiment_folders_cell))
  endfor

				% fprintf(log_handler, '\n');
  fprintf(1, '\n');

  cd(current_path);

  max_sequences_length = 500;
  lengths_vector = 10:10:max_sequences_length;

  if 1 % Substitute by 0 if the parameters of the genuine drift distribution are estimated from 3-parties.
    % Genuine drift distribution are estimated from 2-parties setup
    average_delay = 0;
    average_squared_delay = 0;
% fprintf(log_handler, 'Estimating delay characteristic function...\n');
    fprintf(1, 'Estimating delay characteristic function...\n');
    total_samples = 0;
    for train_client_index = 1:train_set_samples_count
      data_transmission_times = sort([data_initiator_to_gateway_train{train_client_index}; data_responder_to_gateway_train{train_client_index}], 'ascend');
      data_reception_times = sort([data_initiator_from_gateway_train{train_client_index}; data_responder_from_gateway_train{train_client_index}], 'ascend');
      ack_transmission_times = data_reception_times;
      ack_reception_times = sort([ack_initiator_from_gateway_train{train_client_index}; ack_responder_from_gateway_train{train_client_index}], 'ascend');
      [aligned_data_transmission_times, aligned_data_reception_times] = align_sequences(data_transmission_times, data_reception_times, average_mixnet_delay);
      [aligned_ack_transmission_times, aligned_ack_reception_times] = align_sequences(ack_transmission_times, ack_reception_times, average_mixnet_delay);
      average_delay = average_delay +  sum(aligned_data_reception_times - aligned_data_transmission_times) + sum(aligned_ack_reception_times - aligned_ack_transmission_times);
      average_squared_delay = average_squared_delay + sum((aligned_data_reception_times - aligned_data_transmission_times).^2) + sum((aligned_ack_reception_times - aligned_ack_transmission_times).^2);
      total_samples = total_samples + length(aligned_data_reception_times) + length(aligned_ack_reception_times);
% fprintf(log_handler, '\rProgress: %i/%i; Partial average delay: %.4f ms; Partial delay standard deviation: %.4f ms.', train_client_index, train_set_samples_count, 1e3*average_delay/(total_samples), ...
%     1e3*sqrt(average_squared_delay/(total_samples) - (average_delay/(total_samples))^2));
      fprintf(1, '\rProgress: %i/%i; Partial average delay: %.4f ms; Partial delay standard deviation: %.4f ms.', train_client_index, train_set_samples_count, 1e3*average_delay/(total_samples), ...
	      1e3*sqrt(average_squared_delay/(total_samples) - (average_delay/(total_samples))^2));
    endfor

    average_delay = average_delay/(total_samples);
    average_squared_delay = average_squared_delay/(total_samples);
    std_dev_delay = sqrt(average_squared_delay - average_delay^2);
% fprintf(log_handler, '\nDone.\tAverage delay: %.4f ms; \tStd deviation of delay: %.4f ms\n', 1e3*average_delay, 1e3*std_dev_delay);
    fprintf(1, '\nDone.\tAverage delay: %.4f ms; \tStd deviation of delay: %.4f ms\n', 1e3*average_delay, 1e3*std_dev_delay);
  else
    % Genuine drift distribution are estimated from 3-parties setup
    average_delay = 0;
    average_squared_delay = 0;
% fprintf(log_handler, 'Estimating delay characteristic function...\n');
    fprintf(1, 'Estimating delay characteristic function...\n');
    total_samples = 0;
    for train_client_index = 1:(train_set_samples_count - 1)
      first_data_transmission_times = sort([data_initiator_to_gateway_train{train_client_index}; data_responder_to_gateway_train{train_client_index}], 'ascend');
      first_data_reception_times = sort([data_initiator_from_gateway_train{train_client_index}; data_responder_from_gateway_train{train_client_index}], 'ascend');
      first_ack_transmission_times = first_data_reception_times;
      first_ack_reception_times = sort([ack_initiator_from_gateway_train{train_client_index}; ack_responder_from_gateway_train{train_client_index}], 'ascend');
      [first_aligned_data_transmission_times, first_aligned_data_reception_times] = align_sequences(first_data_transmission_times, first_data_reception_times, average_mixnet_delay);
      [first_aligned_ack_transmission_times, first_aligned_ack_reception_times] = align_sequences(first_ack_transmission_times, first_ack_reception_times, average_mixnet_delay);
      second_data_transmission_times = sort([data_initiator_to_gateway_train{train_client_index + 1}; data_responder_to_gateway_train{train_client_index + 1}], 'ascend');
      second_data_reception_times = sort([data_initiator_from_gateway_train{train_client_index + 1}; data_responder_from_gateway_train{train_client_index + 1}], 'ascend');
      second_ack_transmission_times = second_data_reception_times;
      second_ack_reception_times = sort([ack_initiator_from_gateway_train{train_client_index + 1}; ack_responder_from_gateway_train{train_client_index + 1}], 'ascend');
      [second_aligned_data_transmission_times, second_aligned_data_reception_times] = align_sequences(second_data_transmission_times, second_data_reception_times, average_mixnet_delay);
      [second_aligned_ack_transmission_times, second_aligned_ack_reception_times] = align_sequences(second_ack_transmission_times, second_ack_reception_times, average_mixnet_delay);
				% Merging
      merged_aligned_data_transmission_times = sort([first_aligned_data_transmission_times; second_aligned_data_transmission_times], 'ascend');
      merged_aligned_ack_transmission_times = sort([first_aligned_ack_transmission_times; second_aligned_ack_transmission_times], 'ascend');
      merged_aligned_data_reception_times = sort([first_aligned_data_reception_times; second_aligned_data_reception_times], 'ascend');
      merged_aligned_ack_reception_times = sort([first_aligned_ack_reception_times; second_aligned_ack_reception_times], 'ascend');
      average_delay = average_delay +  sum(merged_aligned_data_reception_times - merged_aligned_data_transmission_times) + sum(merged_aligned_ack_reception_times - merged_aligned_ack_transmission_times);
      average_squared_delay = average_squared_delay + sum((merged_aligned_data_reception_times - merged_aligned_data_transmission_times).^2) + sum((merged_aligned_ack_reception_times - merged_aligned_ack_transmission_times).^2);
      total_samples = total_samples + length(merged_aligned_data_reception_times) + length(merged_aligned_ack_reception_times);
% fprintf(log_handler, '\rProgress: %i/%i; Partial average delay: %.4f ms; Partial delay standard deviation: %.4f ms.', train_client_index, train_set_samples_count, 1e3*average_delay/(total_samples), ...
%     1e3*sqrt(average_squared_delay/(total_samples) - (average_delay/(total_samples))^2));
      fprintf(1, '\rProgress: %i/%i; Partial average delay: %.4f ms; Partial delay standard deviation: %.4f ms.', train_client_index, train_set_samples_count, 1e3*average_delay/(total_samples), ...
	      1e3*sqrt(average_squared_delay/(total_samples) - (average_delay/(total_samples))^2));
    endfor

    average_delay = average_delay/(total_samples);
    average_squared_delay = average_squared_delay/(total_samples);
    std_dev_delay = sqrt(average_squared_delay - average_delay^2);
% fprintf(log_handler, '\nDone.\tAverage delay: %.4f ms; \tStd deviation of delay: %.4f ms\n', 1e3*average_delay, 1e3*std_dev_delay);
    fprintf(1, '\nDone.\tAverage delay: %.4f ms; \tStd deviation of delay: %.4f ms\n', 1e3*average_delay, 1e3*std_dev_delay);
  endif


  % if 0 % Substitute 0 by 1 if you want to do the experiment on validation set
% fprintf(log_handler, 'Computing score matrix for validation set...\n'); fflush(1);
    fprintf(1, 'Computing score matrix for validation set...\n'); fflush(1);

    client_block_size = ceil(validation_set_samples_count/client_subsets_count);
    client_indexes = (1:client_block_size) + (client_subset_index - 1)*client_block_size;
    if client_indexes(end) > validation_set_samples_count
      client_indexes = client_indexes(1):validation_set_samples_count;
    endif
    clients_count = length(client_indexes);
    server_indexes = 1:validation_set_samples_count;

    score_matrix_validation = zeros(clients_count, validation_set_samples_count, length(lengths_vector) + 1);
    data_mean_delay_validation = zeros(clients_count, validation_set_samples_count);
    data_min_delay_validation = zeros(clients_count, validation_set_samples_count);
    ack_mean_delay_validation = zeros(clients_count, validation_set_samples_count);
    ack_min_delay_validation = zeros(clients_count, validation_set_samples_count);

    for client_index = client_indexes
      max_false_attempt_to_this_client_score = -Inf;
      for server_index = server_indexes
	data_transmission_times = sort([data_initiator_to_gateway_validation{client_index}; data_responder_to_gateway_validation{server_index}], 'ascend');
	data_reception_times = sort([data_initiator_from_gateway_validation{client_index}; data_responder_from_gateway_validation{server_index}], 'ascend');
	ack_transmission_times = data_reception_times;
	ack_reception_times = sort([ack_initiator_from_gateway_validation{client_index}; ack_responder_from_gateway_validation{server_index}], 'ascend');
	[aligned_data_transmission_times, aligned_data_reception_times] = align_sequences(data_transmission_times, data_reception_times, average_mixnet_delay);
	[aligned_ack_transmission_times, aligned_ack_reception_times] = align_sequences(ack_transmission_times, ack_reception_times, average_mixnet_delay);
	data_mean_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index) = mean(aligned_data_reception_times - aligned_data_transmission_times);
	ack_mean_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index) = mean(aligned_ack_reception_times - aligned_ack_transmission_times);
	data_min_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index) = min(aligned_data_reception_times - aligned_data_transmission_times);
	ack_min_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index) = min(aligned_ack_reception_times - aligned_ack_transmission_times);
% fprintf(log_handler, '\rClient %4i, Server %4i.\tMin data delay = %7.2f ms;\tMin ack delay: %7.2f ms;\tMean data delay = %7.2f ms;\tMean ack delay: %7.2f ms.\t', client_index, server_index, 1e3*data_min_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index), 1e3*ack_min_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index), 1e3*data_mean_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index), 1e3*ack_mean_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index));
	fprintf(1, '\rClient %4i, Server %4i.\tMin data delay = %7.2f ms;\tMin ack delay: %7.2f ms;\tMean data delay = %7.2f ms;\tMean ack delay: %7.2f ms.\t', client_index, server_index, 1e3*data_min_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index), 1e3*ack_min_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index), 1e3*data_mean_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index), 1e3*ack_mean_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index));
	assert(ack_mean_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index) >= average_mixnet_delay)
	assert(data_mean_delay_validation(mod(client_index - 1, client_block_size) + 1, server_index) >= average_mixnet_delay)
	min_length = min([length(aligned_data_transmission_times); length(aligned_ack_transmission_times)]);
	score_vector = ...
	gaussian_delay_sequences_match_loglikelihood(aligned_data_transmission_times(1:min_length), aligned_data_reception_times(1:min_length), average_delay, std_dev_delay, true) + ...
	gaussian_delay_sequences_match_loglikelihood(aligned_ack_transmission_times(1:min_length), aligned_ack_reception_times(1:min_length), average_delay, std_dev_delay, true);
	for k=1:length(lengths_vector)
	  score_matrix_validation(mod(client_index - 1, client_block_size) + 1, server_index, k) = mean(score_vector(1:lengths_vector(k)));
	endfor
	score_matrix_validation(mod(client_index - 1, client_block_size) + 1, server_index, end) = mean(score_vector);
	if client_index == server_index
% fprintf(log_handler, ['LLK(%4i) = ' char(27) '[1;49;92m%.2f' char(27) '[0m. \t'], min_length, score_matrix_validation(mod(client_index - 1, client_block_size) + 1, server_index, end)); fflush(1);
	  fprintf(1, ['LLK(%4i) = ' char(27) '[1;49;92m%.2f' char(27) '[0m. \t'], min_length, score_matrix_validation(mod(client_index - 1, client_block_size) + 1, server_index, end)); fflush(1);
	else
% fprintf(log_handler, ['LLK(%4i) = ' char(27) '[0;49;31m%.2f' char(27) '[0m. \t'], min_length, score_matrix_validation(mod(client_index - 1, client_block_size) + 1, server_index, end)); fflush(1);
	  fprintf(1, ['LLK(%4i) = ' char(27) '[0;49;31m%.2f' char(27) '[0m. \t'], min_length, score_matrix_validation(mod(client_index - 1, client_block_size) + 1, server_index, end)); fflush(1);
	endif
% fprintf(log_handler, 'Progress: %.6f%%', 100.0*double(mod(client_index - 1, client_block_size)*validation_set_samples_count + server_index)/double(validation_set_samples_count*client_block_size)); fflush(1);
	fprintf(1, 'Progress: %.6f%%', 100.0*double(mod(client_index - 1, client_block_size)*validation_set_samples_count + server_index)/double(validation_set_samples_count*client_block_size)); fflush(1);
	if client_index == server_index
			      % fprintf(log_handler, '\n'); fflush(1);
	  fprintf(1, '\n'); fflush(1);
	else
	  if max_false_attempt_to_this_client_score < score_matrix_validation(mod(client_index - 1, client_block_size) + 1, server_index, end)
	    max_false_attempt_to_this_client_score = score_matrix_validation(mod(client_index - 1, client_block_size) + 1, server_index, end);
			      % fprintf(log_handler, '\n'); fflush(1);
	    fprintf(1, '\n'); fflush(1);
	  endif
	endif
      endfor
    endfor
				% fprintf(log_handler, '\nDone!\n');
    fprintf(1, '\nDone!\n');

    save('-binary', '-z', [results_path '/' database_id '/real_data_experiment_alt_delay_characteristic_validation_' num2str(client_subset_index) '_of_' num2str(client_subsets_count) '_' database_id '.oct'], ...
	 'score_matrix_validation', 'data_mean_delay_validation', 'data_min_delay_validation', 'ack_mean_delay_validation', 'ack_min_delay_validation', ...
	 'train_experiment_folders_cell', 'validation_experiment_folders_cell');
    clear score_matrix_validation;
    clear data_mean_delay_validation;
    clear data_min_delay_validation;
    clear ack_mean_delay_validation;
    clear ack_min_delay_validation;
    clear data_initiator_from_gateway_validation;
    clear data_initiator_to_gateway_validation;
    clear data_responder_from_gateway_validation;
    clear data_responder_to_gateway_validation;
    clear ack_initiator_from_gateway_validation;
    clear ack_responder_from_gateway_validation;
    clear validation_experiment_folders_cell;
  % endif

% fprintf(log_handler, 'Computing score matrix for test set...\n'); fflush(1);
  fprintf(1, 'Computing score matrix for test set...\n'); fflush(1)
  fprintf(1, 'In this experiment the score matrix size is %i x %i\n', ...
	  test_set_samples_count - 1, test_set_samples_count - 1);

  client_block_size = ceil((test_set_samples_count - 1)/client_subsets_count);
  client_indexes = (1:client_block_size) + (client_subset_index - 1)*client_block_size;
  if client_indexes(end) > (test_set_samples_count - 1)
    client_indexes = client_indexes(1):(test_set_samples_count - 1);
  endif
  clients_count = length(client_indexes);
  server_indexes = 1:(test_set_samples_count - 1);

  score_matrix_test = zeros(clients_count, test_set_samples_count - 1, length(lengths_vector) + 1);
  data_mean_delay_test = zeros(clients_count, test_set_samples_count - 1);
  data_min_delay_test = zeros(clients_count, test_set_samples_count - 1);
  ack_mean_delay_test = zeros(clients_count, test_set_samples_count - 1);
  ack_min_delay_test = zeros(clients_count, test_set_samples_count - 1);

  for client_index = client_indexes
    max_false_attempt_to_this_client_score = -Inf;
    for server_index = server_indexes
% Here it is where the main difference between normal experiments and 3-parties happens
% client_index corresponds to the index of the first responder,
% mod(client_index, test_set_samples_count) + 1 corresponds to the index of the second responder
% server_index is used to navigate through the initiators.
% Regarding the initiator indexes, for the unmatched negatives experiment:
% first_initiator_index is the server_index if server_index <= client_index
% first_initiator_index is server_index + 1 if server_index > client_index
% second_initiator_index is mod(server_index + 1, test_set_samples_count) + 1 if server_index > client_index
% second_initiator_index is client_index + 1 if server_index == client_index
% second_initiator_index is mod(client_index + 2, test_set_samples_count) + 1 if server_index == client_index - 1
%
% second_initiator_index is client_index + 1 otherwise.
      first_responder_index = client_index;
      second_responder_index = mod(client_index, test_set_samples_count) + 1;
      if server_index <= client_index
	first_initiator_index = server_index;
      else
	first_initiator_index = server_index + 1;
      endif
      if server_index == client_index
	second_initiator_index = second_responder_index;
      elseif server_index == client_index - 1
	second_initiator_index = mod(client_index + 1, test_set_samples_count) + 1;
      else
	second_initiator_index = mod(server_index + 1, test_set_samples_count) + 1;
	if second_initiator_index == first_initiator_index
	  second_initiator_index = second_initiator_index + 2;
	endif
      endif

      first_data_transmission_times = sort([data_initiator_to_gateway_test{first_initiator_index}; data_responder_to_gateway_test{first_responder_index}], 'ascend');
      first_data_reception_times = sort([data_initiator_from_gateway_test{first_initiator_index}; data_responder_from_gateway_test{first_responder_index}], 'ascend');
      first_ack_transmission_times = first_data_reception_times;
      first_ack_reception_times = sort([ack_initiator_from_gateway_test{first_initiator_index}; ack_responder_from_gateway_test{first_responder_index}], 'ascend');

      [first_aligned_data_transmission_times, first_aligned_data_reception_times] = align_sequences(first_data_transmission_times, first_data_reception_times, average_mixnet_delay);
      [first_aligned_ack_transmission_times, first_aligned_ack_reception_times] = align_sequences(first_ack_transmission_times, first_ack_reception_times, average_mixnet_delay);

      second_data_transmission_times = sort([data_initiator_to_gateway_test{second_initiator_index}; data_responder_to_gateway_test{second_responder_index}], 'ascend');
      second_data_reception_times = sort([data_initiator_from_gateway_test{second_initiator_index}; data_responder_from_gateway_test{second_responder_index}], 'ascend');
      second_ack_transmission_times = second_data_reception_times;
      second_ack_reception_times = sort([ack_initiator_from_gateway_test{second_initiator_index}; ack_responder_from_gateway_test{second_responder_index}], 'ascend');

      [second_aligned_data_transmission_times, second_aligned_data_reception_times] = align_sequences(second_data_transmission_times, second_data_reception_times, average_mixnet_delay);
      [second_aligned_ack_transmission_times, second_aligned_ack_reception_times] = align_sequences(second_ack_transmission_times, second_ack_reception_times, average_mixnet_delay);

      merged_aligned_data_transmission_times = sort([first_aligned_data_transmission_times; second_aligned_data_transmission_times], 'ascend');
      merged_aligned_ack_transmission_times = sort([first_aligned_ack_transmission_times; second_aligned_ack_transmission_times], 'ascend');
      merged_aligned_data_reception_times = sort([first_aligned_data_reception_times; second_aligned_data_reception_times], 'ascend');
      merged_aligned_ack_reception_times = sort([first_aligned_ack_reception_times; second_aligned_ack_reception_times], 'ascend');

      data_mean_delay_test(mod(client_index - 1, client_block_size) + 1, server_index) = mean(merged_aligned_data_reception_times - merged_aligned_data_transmission_times);
      ack_mean_delay_test(mod(client_index - 1, client_block_size) + 1, server_index) = mean(merged_aligned_ack_reception_times - merged_aligned_ack_transmission_times);
      data_min_delay_test(mod(client_index - 1, client_block_size) + 1, server_index) = min(merged_aligned_data_reception_times - merged_aligned_data_transmission_times);
      ack_min_delay_test(mod(client_index - 1, client_block_size) + 1, server_index) = min(merged_aligned_ack_reception_times - merged_aligned_ack_transmission_times);
      min_length = min([length(merged_aligned_data_transmission_times); length(merged_aligned_ack_transmission_times)]);
      score_vector =  gaussian_delay_sequences_match_loglikelihood(merged_aligned_data_transmission_times(1:min_length), ...
								   merged_aligned_data_reception_times(1:min_length), average_delay, std_dev_delay, true) + ...
		      gaussian_delay_sequences_match_loglikelihood(merged_aligned_ack_transmission_times(1:min_length), ...
								   merged_aligned_ack_reception_times(1:min_length), average_delay, std_dev_delay, true);

      for k=1:length(lengths_vector)
	score_matrix_test(mod(client_index - 1, client_block_size) + 1, server_index, k) = mean(score_vector(1:lengths_vector(k)));
      endfor
      score_matrix_test(mod(client_index - 1, client_block_size) + 1, server_index, end) = mean(score_vector);
      fprintf(1, '\rResponders %05i-%05i; Initiators %05i-%05i; ', first_responder_index, second_responder_index, first_initiator_index, second_initiator_index);
      fprintf(1, 'Min/Mean data delay: %7.2f/%7.2f ms; ', ...
	      1e3*data_min_delay_test(mod(client_index - 1, client_block_size) + 1, server_index), ...
	      1e3*data_mean_delay_test(mod(client_index - 1, client_block_size) + 1, server_index));
      fprintf(1, 'Min/Mean ack delay: %7.2f/%7.2f ms; ', ...
	      1e3*ack_min_delay_test(mod(client_index - 1, client_block_size) + 1, server_index), ...
	      1e3*ack_mean_delay_test(mod(client_index - 1, client_block_size) + 1, server_index));

      if client_index == server_index
	fprintf(1, ['LLK(%4i) = ' char(27) '[1;49;92m%.2f' char(27) '[0m. \t'], min_length, score_matrix_test(mod(client_index - 1, client_block_size) + 1, server_index, end)); fflush(1);
      else
	fprintf(1, ['LLK(%4i) = ' char(27) '[0;49;31m%.2f' char(27) '[0m. \t'], min_length, score_matrix_test(mod(client_index - 1, client_block_size) + 1, server_index, end)); fflush(1);
      endif
      fprintf(1, 'Progress: %.6f%%', 100.0*double(mod(client_index - 1, client_block_size)*test_set_samples_count + server_index)/double(test_set_samples_count*client_block_size)); fflush(1);

      if client_index == server_index
			      % fprintf(log_handler, '\n'); fflush(1);
	fprintf(1, '\n'); fflush(1);
      else
	if max_false_attempt_to_this_client_score < score_matrix_test(mod(client_index - 1, client_block_size) + 1, server_index, end)
	  max_false_attempt_to_this_client_score = score_matrix_test(mod(client_index - 1, client_block_size) + 1, server_index, end);
			      % fprintf(log_handler, '\n'); fflush(1);
	  fprintf(1, '\n'); fflush(1);
	endif
      endif
    endfor
  endfor
				% fprintf(log_handler, '\nDone!\n');
  fprintf(1, '\nDone!\n');
				% fclose(log_handler);

  save('-binary', '-z', ...
       [results_path '/' database_id '/real_data_experiment_alt_delay_characteristic_test_' ...
		     num2str(client_subset_index) '_of_' num2str(client_subsets_count) ...
		     '_' database_id '_3parties_semimatched.oct'], ...
       'score_matrix_test', 'data_mean_delay_test', 'data_min_delay_test', 'ack_mean_delay_test', 'ack_min_delay_test', ...
       'train_experiment_folders_cell', 'test_experiment_folders_cell');

endfunction
