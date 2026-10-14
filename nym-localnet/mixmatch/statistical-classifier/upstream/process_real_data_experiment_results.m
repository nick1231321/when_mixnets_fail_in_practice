database_folder_path = '../datasets/baseline';
client_subsets_count = 16;
max_sequences_length = 1000;
experiments_count = 4096;
lengths_vector = 10:10:max_sequences_length;
color_codes = ['k','r','g','b','y','m','c'];
line_style_codes{1} = '-';
line_style_codes{2} = '--';

if ~exist('global_score_matrix', 'var')
  fprintf(1, 'Loading real data...\n');
  for client_subset_index =1:client_subsets_count
    fprintf(1, '\r\tProgress: %.2f%%', 100*(client_subset_index - 1)/client_subsets_count); fflush(1);
    experiment_data_file_name = [database_folder_path '/real_data_experiment_' num2str(client_subset_index) '_of_' num2str(client_subsets_count) '.oct'];
    load('-binary', experiment_data_file_name);
    if ~exist('global_ack_mean_delay', 'var')
      global_ack_mean_delay = zeros(size(ack_mean_delay, 1)*client_subsets_count, size(ack_mean_delay, 2));
    endif
    if ~exist('global_ack_min_delay', 'var')
      global_ack_min_delay = zeros(size(ack_min_delay, 1)*client_subsets_count, size(ack_min_delay, 2));
    endif
    if ~exist('global_data_mean_delay', 'var')
      global_data_mean_delay = zeros(size(data_mean_delay, 1)*client_subsets_count, size(data_mean_delay, 2));
    endif
    if ~exist('global_data_min_delay', 'var')
      global_ack_min_delay = zeros(size(ack_min_delay, 1)*client_subsets_count, size(ack_min_delay, 2));
    endif
    if ~exist('global_score_matrix', 'var')
      global_score_matrix = zeros(size(score_matrix, 1)*client_subsets_count, size(score_matrix, 2), size(score_matrix, 3));
    endif
    global_ack_mean_delay(client_indexes, :) = ack_mean_delay;
    global_ack_min_delay(client_indexes, :) = ack_min_delay;
    global_data_mean_delay(client_indexes, :) = data_mean_delay;
    global_data_min_delay(client_indexes, :) = data_min_delay;
    global_score_matrix(client_indexes, :, :) = score_matrix;
    fprintf(1, '\r\tProgress: %.2f%%', 100*client_subset_index/client_subsets_count); fflush(1);
  endfor
  clear ack_mean_delay;
  clear ack_min_delay;
  clear data_mean_delay;
  clear data_min_delay;
  clear score_matrix;
  fprintf(1, 'Done!\n');
endif

thr = zeros(length(lengths_vector) + 1, 1);
FAR = zeros(length(lengths_vector) + 1, 1);
FRR = zeros(length(lengths_vector) + 1, 1);
EER = zeros(length(lengths_vector) + 1, 1);
trivial_false_matches_count = zeros(length(lengths_vector) + 1, 1);
trivial_true_matches_fail_count = zeros(length(lengths_vector) + 1, 1);
figure();
hold on;
Set_DET_limits(5e-4, 0.4, 5e-5, 0.2);
plotted_curves_count = 0;
for length_index = 1:length(lengths_vector)
  plot_DET_curve = (mod(lengths_vector(length_index), 100) == 0);
  if plot_DET_curve
    plotted_curves_count = plotted_curves_count + 1;
  endif
  sequences_length = lengths_vector(length_index);
  fprintf(1, 'Analysing results using sequences of length %i...\n', sequences_length);
  fprintf(1, '\tReading true scores from score matrix... '); fflush(1);
  true_matches_scores = diag(global_score_matrix(1:experiments_count, 1:experiments_count, length_index));
  fprintf(1, 'Done.\n');
  fprintf(1, '\tReading false scores from score matrix... '); fflush(1);
  false_matches_scores = zeros(experiments_count*(experiments_count - 1), 1);
  added_false_matches_scores = 0;
  for diag_index = 1:(experiments_count - 1)
    false_matches_scores_diag = diag(global_score_matrix(1:experiments_count, 1:experiments_count, length_index), -diag_index);
    scores_count = length(false_matches_scores_diag);
    false_matches_scores(added_false_matches_scores + (1:scores_count)) = false_matches_scores_diag;
    added_false_matches_scores = added_false_matches_scores + scores_count;
    false_matches_scores_diag = diag(global_score_matrix(1:experiments_count, 1:experiments_count, length_index), diag_index);
    scores_count = length(false_matches_scores_diag);
    false_matches_scores(added_false_matches_scores + (1:scores_count)) = false_matches_scores_diag;
    added_false_matches_scores = added_false_matches_scores + scores_count;
  endfor
  assert(added_false_matches_scores == experiments_count*(experiments_count - 1));
  fprintf(1, 'Done.\n');
  trivial_false_matches_count(length_index) = sum(false_matches_scores == -Inf);
  trivial_true_matches_fail_count(length_index) = sum(true_matches_scores == -Inf);
  trivial_true_matches_fail_locations{length_index} = find(true_matches_scores == -Inf);
  false_matches_count = length(false_matches_scores);
  true_matches_count = length(true_matches_scores);
  fprintf(1, '\tPerformance:\n');
  fprintf(1, '\t\tTrivial false matches rate:     \t%.5f%%\n', 100*trivial_false_matches_count(length_index)/false_matches_count);
  fprintf(1, '\t\tTrivial true matches fail rate: \t%.5f%%\n', 100*trivial_true_matches_fail_count(length_index)/true_matches_count);
  false_matches_scores(false_matches_scores == -Inf) = min([false_matches_scores(false_matches_scores ~= -Inf); true_matches_scores(true_matches_scores ~= -Inf)]) - 1.0;
  true_matches_scores(true_matches_scores == -Inf) = min([false_matches_scores(false_matches_scores ~= -Inf); true_matches_scores(true_matches_scores ~= -Inf)]) - 1.0;
  [thr(length_index), FAR(length_index), FRR(length_index)] = getThresholdEER(true_matches_scores, false_matches_scores);
  fprintf(1, '\t\tFalse Alarms Rate @EER:         \t%.2f%%\n', 100*FAR(length_index));
  fprintf(1, '\t\tFalse Miss Rate @EER:           \t%.2f%%\n', 100*FRR(length_index));
  fprintf(1, '\t\tEqual Error Rate (EER):         \t%.2f%%\n', 100*EER(length_index));
  if plot_DET_curve
    [Pmiss, Pfa] = Compute_DET(true_matches_scores, false_matches_scores);
    line_color = color_codes(mod(plotted_curves_count - 1, length(color_codes)) + 1);
    line_style = line_style_codes{mod(int32(floor((plotted_curves_count - 1)/length(color_codes))), 2) + 1};
    Plot_DET(Pmiss, Pfa, ['2' line_color line_style ';Sequences length = ' num2str(sequences_length) ';']);
  endif
endfor

fprintf(1, 'Analysing results using full sequences...\n');
fprintf(1, '\tReading true scores from score matrix... '); fflush(1);
true_matches_scores = diag(global_score_matrix(1:experiments_count, 1:experiments_count, end));
fprintf(1, 'Done.\n');
fprintf(1, '\tReading false scores from score matrix... '); fflush(1);
false_matches_scores = zeros(experiments_count*(experiments_count - 1), 1);
added_false_matches_scores = 0;
for diag_index = 1:(experiments_count - 1)
  false_matches_scores_diag = diag(global_score_matrix(1:experiments_count, 1:experiments_count, end), -diag_index);
  scores_count = length(false_matches_scores_diag);
  false_matches_scores(added_false_matches_scores + (1:scores_count)) = false_matches_scores_diag;
  added_false_matches_scores = added_false_matches_scores + scores_count;
  false_matches_scores_diag = diag(global_score_matrix(1:experiments_count, 1:experiments_count, end), diag_index);
  scores_count = length(false_matches_scores_diag);
  false_matches_scores(added_false_matches_scores + (1:scores_count)) = false_matches_scores_diag;
  added_false_matches_scores = added_false_matches_scores + scores_count;
endfor
assert(added_false_matches_scores == experiments_count*(experiments_count - 1));
fprintf(1, 'Done.\n');
trivial_false_matches_count(end) = sum(false_matches_scores == -Inf);
trivial_true_matches_fail_count(end) = sum(true_matches_scores == -Inf);
trivial_true_matches_fail_locations{length(lengths_vector) + 1} = find(true_matches_scores == -Inf);
false_matches_count = length(false_matches_scores);
true_matches_count = length(true_matches_scores);
fprintf(1, '\tPerformance:\n');
fprintf(1, '\t\tTrivial false matches rate:     \t%.5f%%\n', 100*trivial_false_matches_count(end)/false_matches_count);
fprintf(1, '\t\tTrivial true matches fail rate: \t%.5f%%\n', 100*trivial_true_matches_fail_count(end)/true_matches_count);
false_matches_scores(false_matches_scores == -Inf) = min([false_matches_scores(false_matches_scores ~= -Inf); true_matches_scores(true_matches_scores ~= -Inf)]) - 1.0;
true_matches_scores(true_matches_scores == -Inf) = min([false_matches_scores(false_matches_scores ~= -Inf); true_matches_scores(true_matches_scores ~= -Inf)]) - 1.0;
[thr(end), FAR(end), FRR(end)] = getThresholdEER(true_matches_scores, false_matches_scores);
fprintf(1, '\t\tFalse Alarms Rate @EER:       \t%.2f%%\n', 100*FAR(end));
fprintf(1, '\t\tFalse Miss Rate @EER:         \t%.2f%%\n', 100*FRR(end));
fprintf(1, '\t\tEqual Error Rate (EER):       \t%.2f%%\n', 100*EER(end));
plotted_curves_count = plotted_curves_count + 1;
[Pmiss, Pfa] = Compute_DET(true_matches_scores, false_matches_scores);
line_color = color_codes(mod(plotted_curves_count - 1, length(color_codes)) + 1);
line_style = line_style_codes{mod(int32(floor((plotted_curves_count - 1)/length(color_codes))), 2) + 1};
Plot_DET(Pmiss, Pfa, ['2' line_color line_style ';Full sequences;']);

legend('location', 'eastoutside');
