package main

import (
	"testing"

	"github.com/mattermost/mattermost/server/public/model"
	"github.com/mattermost/mattermost/server/public/plugin/plugintest"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/mock"
)

// TestUploadFileWithUserID_ValidUser tests successful file upload with valid user ID
func TestUploadFileWithUserID_ValidUser(t *testing.T) {
	api := &plugintest.API{}
	plugin := &Plugin{}
	plugin.SetAPI(api)

	// Mock data
	channelID := "channel123"
	filename := "test.txt"
	userID := "user123"
	data := []byte("test file content")
	
	originalFileInfo := &model.FileInfo{
		Id:        "file123",
		CreatorId: model.UploadNoUserID,
		Name:      filename,
	}
	
	newFileInfo := &model.FileInfo{
		Id:        "file456",
		CreatorId: userID,
		Name:      filename,
	}
	
	user := &model.User{
		Id:       userID,
		Username: "testuser",
	}

	// Set up expectations
	api.On("UploadFile", data, channelID, filename).Return(originalFileInfo, nil)
	api.On("GetUser", userID).Return(user, nil)
	api.On("CopyFileInfos", userID, []string{"file123"}).Return([]string{"file456"}, nil)
	api.On("GetFileInfo", "file456").Return(newFileInfo, nil)
	api.On("LogDebug", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return()

	// Execute
	result, err := plugin.uploadFileWithUserID(data, channelID, filename, userID)

	// Assert
	assert.Nil(t, err)
	assert.NotNil(t, result)
	assert.Equal(t, "file456", result.Id)
	assert.Equal(t, userID, result.CreatorId)
	
	api.AssertExpectations(t)
}

// TestUploadFileWithUserID_EmptyUserID tests upload with empty user ID
func TestUploadFileWithUserID_EmptyUserID(t *testing.T) {
	api := &plugintest.API{}
	plugin := &Plugin{}
	plugin.SetAPI(api)

	channelID := "channel123"
	filename := "test.txt"
	data := []byte("test file content")
	
	fileInfo := &model.FileInfo{
		Id:        "file123",
		CreatorId: model.UploadNoUserID,
		Name:      filename,
	}

	// Set up expectations - should only call UploadFile
	api.On("UploadFile", data, channelID, filename).Return(fileInfo, nil)

	// Execute with empty user ID
	result, err := plugin.uploadFileWithUserID(data, channelID, filename, "")

	// Assert - should return original file without trying to copy
	assert.Nil(t, err)
	assert.NotNil(t, result)
	assert.Equal(t, "file123", result.Id)
	assert.Equal(t, model.UploadNoUserID, result.CreatorId)
	
	api.AssertExpectations(t)
}

// TestUploadFileWithUserID_NoUserID tests upload with model.UploadNoUserID
func TestUploadFileWithUserID_NoUserID(t *testing.T) {
	api := &plugintest.API{}
	plugin := &Plugin{}
	plugin.SetAPI(api)

	channelID := "channel123"
	filename := "test.txt"
	data := []byte("test file content")
	
	fileInfo := &model.FileInfo{
		Id:        "file123",
		CreatorId: model.UploadNoUserID,
		Name:      filename,
	}

	// Set up expectations
	api.On("UploadFile", data, channelID, filename).Return(fileInfo, nil)

	// Execute with model.UploadNoUserID
	result, err := plugin.uploadFileWithUserID(data, channelID, filename, model.UploadNoUserID)

	// Assert
	assert.Nil(t, err)
	assert.NotNil(t, result)
	assert.Equal(t, "file123", result.Id)
	assert.Equal(t, model.UploadNoUserID, result.CreatorId)
	
	api.AssertExpectations(t)
}

// TestUploadFileWithUserID_UserValidationFails tests graceful fallback when user doesn't exist
func TestUploadFileWithUserID_UserValidationFails(t *testing.T) {
	api := &plugintest.API{}
	plugin := &Plugin{}
	plugin.SetAPI(api)

	channelID := "channel123"
	filename := "test.txt"
	userID := "nonexistent"
	data := []byte("test file content")
	
	fileInfo := &model.FileInfo{
		Id:        "file123",
		CreatorId: model.UploadNoUserID,
		Name:      filename,
	}

	// Set up expectations
	api.On("UploadFile", data, channelID, filename).Return(fileInfo, nil)
	api.On("GetUser", userID).Return(nil, model.NewAppError("test", "app.user.get.app_error", nil, "", 404))
	api.On("LogWarn", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return()

	// Execute
	result, err := plugin.uploadFileWithUserID(data, channelID, filename, userID)

	// Assert - should return original file with system user
	assert.Nil(t, err)
	assert.NotNil(t, result)
	assert.Equal(t, "file123", result.Id)
	assert.Equal(t, model.UploadNoUserID, result.CreatorId)
	
	api.AssertExpectations(t)
}

// TestUploadFileWithUserID_CopyFileInfosFails tests graceful fallback when CopyFileInfos fails
func TestUploadFileWithUserID_CopyFileInfosFails(t *testing.T) {
	api := &plugintest.API{}
	plugin := &Plugin{}
	plugin.SetAPI(api)

	channelID := "channel123"
	filename := "test.txt"
	userID := "user123"
	data := []byte("test file content")
	
	fileInfo := &model.FileInfo{
		Id:        "file123",
		CreatorId: model.UploadNoUserID,
		Name:      filename,
	}
	
	user := &model.User{
		Id:       userID,
		Username: "testuser",
	}

	// Set up expectations
	api.On("UploadFile", data, channelID, filename).Return(fileInfo, nil)
	api.On("GetUser", userID).Return(user, nil)
	api.On("CopyFileInfos", userID, []string{"file123"}).Return(nil, model.NewAppError("test", "app.file.copy.app_error", nil, "", 500))
	api.On("LogWarn", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return()

	// Execute
	result, err := plugin.uploadFileWithUserID(data, channelID, filename, userID)

	// Assert - should return original file
	assert.Nil(t, err)
	assert.NotNil(t, result)
	assert.Equal(t, "file123", result.Id)
	assert.Equal(t, model.UploadNoUserID, result.CreatorId)
	
	api.AssertExpectations(t)
}

// TestUploadFileWithUserID_CopyReturnsEmpty tests graceful fallback when CopyFileInfos returns empty array
func TestUploadFileWithUserID_CopyReturnsEmpty(t *testing.T) {
	api := &plugintest.API{}
	plugin := &Plugin{}
	plugin.SetAPI(api)

	channelID := "channel123"
	filename := "test.txt"
	userID := "user123"
	data := []byte("test file content")
	
	fileInfo := &model.FileInfo{
		Id:        "file123",
		CreatorId: model.UploadNoUserID,
		Name:      filename,
	}
	
	user := &model.User{
		Id:       userID,
		Username: "testuser",
	}

	// Set up expectations
	api.On("UploadFile", data, channelID, filename).Return(fileInfo, nil)
	api.On("GetUser", userID).Return(user, nil)
	api.On("CopyFileInfos", userID, []string{"file123"}).Return([]string{}, nil)
	api.On("LogWarn", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return()

	// Execute
	result, err := plugin.uploadFileWithUserID(data, channelID, filename, userID)

	// Assert - should return original file
	assert.Nil(t, err)
	assert.NotNil(t, result)
	assert.Equal(t, "file123", result.Id)
	
	api.AssertExpectations(t)
}

// TestUploadFileWithUserID_GetFileInfoFails tests graceful fallback when GetFileInfo fails
func TestUploadFileWithUserID_GetFileInfoFails(t *testing.T) {
	api := &plugintest.API{}
	plugin := &Plugin{}
	plugin.SetAPI(api)

	channelID := "channel123"
	filename := "test.txt"
	userID := "user123"
	data := []byte("test file content")
	
	fileInfo := &model.FileInfo{
		Id:        "file123",
		CreatorId: model.UploadNoUserID,
		Name:      filename,
	}
	
	user := &model.User{
		Id:       userID,
		Username: "testuser",
	}

	// Set up expectations
	api.On("UploadFile", data, channelID, filename).Return(fileInfo, nil)
	api.On("GetUser", userID).Return(user, nil)
	api.On("CopyFileInfos", userID, []string{"file123"}).Return([]string{"file456"}, nil)
	api.On("GetFileInfo", "file456").Return(nil, model.NewAppError("test", "app.file.get.app_error", nil, "", 404))
	api.On("LogWarn", mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything, mock.Anything).Return()

	// Execute
	result, err := plugin.uploadFileWithUserID(data, channelID, filename, userID)

	// Assert - should return original file
	assert.Nil(t, err)
	assert.NotNil(t, result)
	assert.Equal(t, "file123", result.Id)
	
	api.AssertExpectations(t)
}

// TestUploadFileWithUserID_UploadFails tests error handling when initial upload fails
func TestUploadFileWithUserID_UploadFails(t *testing.T) {
	api := &plugintest.API{}
	plugin := &Plugin{}
	plugin.SetAPI(api)

	channelID := "channel123"
	filename := "test.txt"
	userID := "user123"
	data := []byte("test file content")
	
	expectedErr := model.NewAppError("test", "app.file.upload.app_error", nil, "", 500)

	// Set up expectations
	api.On("UploadFile", data, channelID, filename).Return(nil, expectedErr)

	// Execute
	result, err := plugin.uploadFileWithUserID(data, channelID, filename, userID)

	// Assert - should return the error
	assert.NotNil(t, err)
	assert.Nil(t, result)
	assert.Equal(t, expectedErr, err)
	
	api.AssertExpectations(t)
}
